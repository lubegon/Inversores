"""Scraping del dashboard Growatt (server.growatt.com).

Extrae (por planta):
- Campos del panel de dispositivo (#panel_device / #tb_device_con)
  - Connection Status
  - Campo adyacente (col 4 en tu selector)
- Tooltip de métricas que aparece al hacer hover sobre un ícono del dashboard.

Salida:
- storage/growatt-dashboard.json
- Log: storage/last_growatt_dashboard.log

Recomendado: ejecutar con el wrapper root que hace Login → Scrape:
- python growatt_scrape_dashboard.py

Notas:
- Este scraper NO extrae tablas de energía (solo dashboard y navegación).
- Selectores vienen de tu guía; si el portal cambia, se pueden parametrizar por env.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import RunLogger, dump_debug, env_flag, launch_browser
from .voltage_sqlite import GrowattRow, connect_db, ensure_monitor_table, insert_monitor_row, monitor_table_name
from providers.supabase_client import save_device, save_plant, save_telemetry_reading

DEFAULT_HOME_URL = "https://server.growatt.com/"

# Navegación / selector de plantas
SEL_TOP_PLANT_SEARCH = "#top_plant_search"
SEL_TOP_PLANT_TITLE = "#top_plant_search > div.selectTitle"
SEL_TOP_PLANT_DROPDOWN = "#header_sel_plantstwo"
SEL_TOP_PLANT_DD = "#header_sel_plantstwo dd"

# Panel de dispositivo
SEL_PANEL_DEVICE = "#panel_device"
SEL_TB_DEVICE = "#tb_device_con"

# Tooltip del diagrama (NO requiere hover: la tabla está embebida en el DOM)
SEL_TIPS_BATTERY_TABLE = "div.animPan.animPan3 i.tips.w table"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_sync_status(provider: str, current: int, total: int, status: str = "syncing", base_dir: Path | None = None) -> None:
    try:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2]
        status_file = base_dir / "storage" / "sync_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            json.dumps({
                "provider": provider,
                "total": total,
                "current": current,
                "percentage": int((current / total) * 100) if total > 0 else 100,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }),
            encoding="utf-8"
        )
    except Exception:
        pass


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _s(text: str | None) -> str:
    return (text or "").strip()


def _extract_panel_device(page) -> tuple[str, str, str]:
    """Extrae (device_serial, connection_status, update_time) desde #tb_device_con.

    Nota: en Growatt la tabla puede tener múltiples .deviceBox; tomamos el primero.
    """

    tb = page.locator(SEL_TB_DEVICE)
    box = tb.locator(".deviceBox").first

    device_serial = ""
    try:
        device_serial = _s(box.locator("td.firstTd").first.inner_text())
    except Exception:
        device_serial = ""

    connection_status = ""
    try:
        td = box.locator("td").filter(has_text=re.compile(r"Connection\s+Status", re.I)).first
        # Normalmente está en span > span
        connection_status = _s(td.locator("span span").first.inner_text())
        if not connection_status:
            # Fallback: limpiar el prefijo y dejar el valor.
            raw = _s(td.inner_text())
            connection_status = re.sub(r"(?i)connection\s+status\s*[:：]\s*", "", raw).strip()
    except Exception:
        connection_status = ""

    update_time = ""
    try:
        td = box.locator("td").filter(has_text=re.compile(r"Update\s+Time", re.I)).first
        update_time = _s(td.locator("span").first.inner_text())
        if not update_time:
            raw = _s(td.inner_text())
            update_time = re.sub(r"(?i)update\s+time\s*[:：]\s*", "", raw).strip()
    except Exception:
        update_time = ""

    return device_serial, connection_status, update_time


def _extract_metrics_from_embedded_table(page) -> dict[str, str]:
    """Lee las tablas de métricas embebidas o tooltips en la vista del dashboard Growatt.

    Retorna dict por etiqueta (ej. 'Battery Voltage' -> '56.4V').
    """

    out: dict[str, str] = {}

    # 1. Buscar en la tabla principal embebida (animPan3 i.tips.w table o similares)
    table_locators = [
        SEL_TIPS_BATTERY_TABLE,
        "div.animPan i.tips.w table",
        "i.tips.w table",
        "#panel_device table",
        ".deviceBox table",
        "table:has(td)"
    ]

    table = None
    for loc in table_locators:
        try:
            t = page.locator(loc).filter(has_text=re.compile(r"(Battery|Voltage|Current|AC|PV)", re.I)).first
            if t.count() > 0:
                table = t
                break
        except Exception:
            pass

    if table is not None:
        # Intento con retry backoff para tolerar latencias de carga
        for attempt in range(3):
            try:
                table.wait_for(state="attached", timeout=5_000 * (attempt + 1))
                break
            except Exception:
                if attempt == 2:
                    break
                time.sleep(0.5 * (2 ** attempt))

        try:
            rows = table.locator("tbody tr, tr")
            for i in range(rows.count()):
                tr = rows.nth(i)
                try:
                    tds = tr.locator("td")
                    if tds.count() >= 2:
                        label = _s(tds.nth(0).inner_text())
                        value = _s(tds.nth(1).inner_text())
                        if label and value:
                            value = re.sub(r"\s+", "", value)
                            out[label] = value
                except Exception:
                    continue
        except Exception:
            pass

    # 2. Fallback vía JS para extraer pares Label: Value en cualquier elemento de tips o métricas
    if not out:
        try:
            js_metrics = page.evaluate(
                """() => {
                    const res = {};
                    const rows = Array.from(document.querySelectorAll('i.tips.w table tr, .deviceBox tr, div[class*="tips"] tr, .param-item'));
                    rows.forEach(r => {
                        const tds = r.querySelectorAll('td, span');
                        if (tds.length >= 2) {
                            const k = (tds[0].innerText || '').strip ? tds[0].innerText.trim() : '';
                            const v = (tds[1].innerText || '').strip ? tds[1].innerText.trim() : '';
                            if (k && v && (k.includes('Voltage') || k.includes('Current') || k.includes('Battery') || k.includes('AC') || k.includes('PV'))) {
                                res[k] = v.replace(/\\s+/g, '');
                            }
                        }
                    });
                    return res;
                }"""
            )
            if js_metrics and isinstance(js_metrics, dict):
                for k, v in js_metrics.items():
                    if k not in out and v:
                        out[k] = v
        except Exception:
            pass

    return out


def _row_from_metrics(*, update_time: str, connection_status: str, metrics: dict[str, str]) -> GrowattRow:
    def pick(*keys: str) -> str:
        for k in keys:
            for kk, vv in metrics.items():
                if k.strip().lower() in kk.strip().lower() or kk.strip().lower() in k.strip().lower():
                    return vv
        return ""

    return GrowattRow(
        update_time=update_time,
        connection_status=connection_status,
        battery_voltage=pick("Battery Voltage", "Batt Volt", "Battery Volt"),
        pv1_pv2_voltage=pick("PV1/PV2 Voltage", "PV1 Voltage", "PV Voltage"),
        pv1_pv2_recharging_current=pick("PV1/PV2 Recharging Current", "PV Current", "Recharging Current"),
        total_charge_current=pick("Total Charge Current", "Charge Current"),
        ac_input_voltage_frequency=pick("Ac Input Voltage/Frequency", "AC Input Voltage/Frequency", "AC Input", "Grid Voltage"),
        ac_output_voltage_frequency=pick("AC Output Voltage/Frequency", "Ac Output Voltage/Frequency", "AC Output", "Inverter Voltage"),
    )


def _select_plants(page, log: RunLogger) -> list[str]:
    """Retorna nombres de plantas según el dropdown superior o tarjetas del dashboard."""

    log.step("Abriendo dropdown de plantas")
    # 1. Intentar abrir dropdown superior
    for click_sel in [SEL_TOP_PLANT_TITLE, SEL_TOP_PLANT_SEARCH, "#top_plant_search", ".selectTitle"]:
        try:
            page.locator(click_sel).first.click(timeout=3_000)
            break
        except Exception:
            pass

    try:
        page.locator(f"{SEL_TOP_PLANT_DROPDOWN}, {SEL_TOP_PLANT_DD}").first.wait_for(state="attached", timeout=5_000)
    except Exception:
        pass

    names: list[str] = []
    # Opción A: Leer items del dropdown dd / li
    try:
        dd = page.locator(f"{SEL_TOP_PLANT_DD}, #header_sel_plantstwo dd, #header_sel_plantstwo li")
        count = dd.count()
        for i in range(count):
            txt = (dd.nth(i).inner_text() or "").strip()
            if txt and txt not in names:
                names.append(txt)
    except Exception:
        pass

    # Opción B: Fallback vía JS en document.querySelectorAll
    if not names:
        try:
            js_names = page.evaluate(
                """() => {
                    const els = Array.from(document.querySelectorAll('#header_sel_plantstwo dd, #header_sel_plantstwo li, .plant-name, .plant-title, div[data-plantname]'));
                    return els.map(e => (e.innerText || '').strip()).filter(Boolean);
                }"""
            )
            if js_names and isinstance(js_names, list):
                for nm in js_names:
                    if nm and nm not in names:
                        names.append(nm)
        except Exception:
            pass

    # Opción C: Fallback leyendo títulos de tarjetas del Dashboard en pantalla ("Nodo ...")
    if not names:
        try:
            card_names = page.evaluate(
                """() => {
                    const cards = Array.from(document.querySelectorAll('.plantItem, .plant-box, .deviceBox, div:has(> img)'));
                    const list = [];
                    cards.forEach(c => {
                        const txt = (c.innerText || '').split('\\n')[0].trim();
                        if (txt && (txt.includes('Nodo') || txt.includes('Respaldo') || txt.includes('Planta'))) {
                            list.push(txt);
                        }
                    });
                    return list;
                }"""
            )
            if card_names and isinstance(card_names, list):
                for cn in card_names:
                    if cn and cn not in names:
                        names.append(cn)
        except Exception:
            pass

    # Cerrar dropdown
    try:
        page.locator(SEL_TOP_PLANT_TITLE).first.click(timeout=1_000)
    except Exception:
        pass

    return names


def _js_click_plant_dd(page, idx: int, plant_name: str = "") -> bool:
    try:
        return bool(
            page.evaluate(
                """
([idx, name]) => {
  // 1. Click por lista dd
  const list = Array.from(document.querySelectorAll('#header_sel_plantstwo dd, #header_sel_plantstwo li'));
  if (list[idx]) {
    list[idx].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
  }
  // 2. Click por texto de nombre en tarjetas
  if (name) {
    const cards = Array.from(document.querySelectorAll('.plantItem, .plant-box, .deviceBox, div'));
    const matched = cards.find(c => c.innerText && c.innerText.includes(name));
    if (matched) {
      matched.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
      return true;
    }
  }
  return false;
}
""",
                [idx, plant_name],
            )
        )
    except Exception:
        return False


def _open_plants_dropdown(page) -> None:
    for _ in range(2):
        for click_sel in [SEL_TOP_PLANT_TITLE, SEL_TOP_PLANT_SEARCH, ".selectTitle"]:
            try:
                page.locator(click_sel).first.click(timeout=2_000)
                break
            except Exception:
                pass
        try:
            page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="attached", timeout=3_000)
            return
        except Exception:
            continue


def _select_plant_by_index(page, idx: int, plant_name: str = "") -> None:
    # 1) Intentar JS directo sin bloqueos
    if _js_click_plant_dd(page, idx, plant_name):
        return

    # 2) Intentar click sobre tarjetas del Dashboard
    if plant_name:
        try:
            card = page.locator(f"text='{plant_name}'").first
            if card.is_visible():
                card.click(timeout=3_000)
                return
        except Exception:
            pass

    # 3) Abrir dropdown como último recurso best-effort
    try:
        _open_plants_dropdown(page)
        dd = page.locator(f"{SEL_TOP_PLANT_DD}, #header_sel_plantstwo dd").nth(idx)
        if dd.count() > 0:
            dd.click(timeout=3_000, force=True)
    except Exception:
        pass


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    log = RunLogger(base_dir, log_filename="last_growatt_dashboard.log")

    storage_state_path = base_dir / "storage" / "growatt.json"
    if not storage_state_path.exists():
        log.fail("No existe storage/growatt.json. Ejecuta primero el login.")
        raise SystemExit(2)

    headless = env_flag("HEADLESS", True)
    home_url = (os.getenv("GROWATT_HOME_URL") or DEFAULT_HOME_URL).strip()

    limit_plants = os.getenv("GROWATT_LIMIT_PLANTS")
    limit_n = int(limit_plants) if limit_plants and limit_plants.isdigit() else None

    out_json = base_dir / "storage" / "growatt-dashboard.json"

    # SQLite Voltage Growatt
    conn = connect_db(base_dir)

    try:
        with sync_playwright() as p:
            log.step(f"Lanzando browser (headless={headless})")
            browser = launch_browser(p, headless=headless)
            context = browser.new_context(storage_state=str(storage_state_path))
            page = context.new_page()
            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(60_000)

            try:
                log.step("Abriendo home")
                if "login" in (home_url or "").lower():
                    home_url = "https://server.growatt.com/index"
                page.goto(home_url, wait_until="networkidle", timeout=60_000)

                log.step("Esperando selector superior de plantas")
                try:
                    page.locator(f"{SEL_TOP_PLANT_SEARCH}, #header_sel_plantstwo, .selectTitle, #index, .layui-layout-admin, body").first.wait_for(state="attached", timeout=30_000)
                except Exception:
                    pass

                plant_names = _select_plants(page, log)
                if not plant_names:
                    plant_names = [
                        "Nodo 1er Respaldo Últimas Noticias",
                        "Nodo Monte Cristo I",
                        "Nodo Antimano",
                        "Nodo Provemed 2do Respald",
                        "Nodo Porlamar 2do Respald",
                        "Nodo Casanay",
                        "Nodo CC las Vegas / Petar",
                    ]
                if limit_n is not None:
                    plant_names = plant_names[: max(0, limit_n)]
                log.ok(f"Plantas detectadas: {len(plant_names)}")

                results: list[dict[str, Any]] = []

                ALLOWED_INVERTERS = {"HUEFBJV03H", "TSE7A45046", "HUEFBJV006", "HUEFBJV05N", "TSE7A4504E", "HUEFBJV021"}

                for idx, plant_name in enumerate(plant_names):
                    if not any(inv in plant_name for inv in ALLOWED_INVERTERS):
                        log.step(f"Planta {idx+1}/{len(plant_names)}: {plant_name} (Omitida por filtro)")
                        continue

                    log.step(f"Planta {idx+1}/{len(plant_names)}: {plant_name}")
                    try:
                        _select_plant_by_index(page, idx, plant_name)
                        
                        # Wait explicitly for the React/Vue frontend to update the data for the newly selected plant
                        page.wait_for_timeout(3000)

                        # Confirmar cambio de planta en el panel (best-effort)
                        try:
                            plant_span = (
                                page.locator(SEL_TB_DEVICE)
                                .locator("td")
                                .filter(has_text=re.compile(r"Plant\s+Name", re.I))
                                .locator("span")
                                .first
                            )
                            plant_span.wait_for(state="attached", timeout=3_000)
                        except Exception:
                            pass

                        # Esperar panel del dispositivo.
                        try:
                            page.locator(f"{SEL_PANEL_DEVICE}, {SEL_TB_DEVICE}").first.wait_for(state="attached", timeout=5_000)
                        except Exception:
                            pass

                        # Panel: serial / status / update time
                        device_serial, conn_status, update_time = ("", "", "")
                        try:
                            device_serial, conn_status, update_time = _extract_panel_device(page)
                        except Exception:
                            pass

                        # Tooltip: tabla embebida (sin hover)
                        metrics = _extract_metrics_from_embedded_table(page)
                        if not metrics:
                            log.warn("No encontré tabla de métricas (Battery Voltage) en animPan3")

                        row = _row_from_metrics(
                            update_time=update_time,
                            connection_status=conn_status,
                            metrics=metrics,
                        )

                        # Persistir en SQLite (tabla por monitor/planta+serial)
                        monitor_name = plant_name
                        if device_serial:
                            monitor_name = f"{plant_name}__{device_serial}"

                        table_name = monitor_table_name(monitor_name)
                        ensure_monitor_table(conn, table_name=table_name)
                        insert_monitor_row(conn, table_name=table_name, row=row.as_list())
                        conn.commit()
                        log.ok(f"Planta {idx+1}/{len(plant_names)}: SQLite insertado en {table_name}")

                        # Sincronización remota con Supabase PostgreSQL
                        try:
                            dev_key = device_serial or table_name
                            save_plant(
                                provider="growatt",
                                plant_id=str(idx + 1),
                                name=plant_name,
                                metadata={"table_name": table_name, "device_serial": device_serial},
                            )
                            save_device(
                                provider="growatt",
                                plant_id=str(idx + 1),
                                device_key=dev_key,
                                device_name=monitor_name,
                                device_type="Inverter",
                                metadata={"table_name": table_name},
                            )
                            telemetry_metrics = {
                                "battery_voltage": row.battery_voltage,
                                "pv1_pv2_voltage": row.pv1_pv2_voltage,
                                "pv1_pv2_recharging_current": row.pv1_pv2_recharging_current,
                                "total_charge_current": row.total_charge_current,
                                "ac_input_voltage_frequency": row.ac_input_voltage_frequency,
                                "ac_output_voltage_frequency": row.ac_output_voltage_frequency,
                                "table_name": table_name,
                                "plant_name": plant_name,
                                "device_name": monitor_name,
                                "device_serial": device_serial,
                                "Battery Voltage": row.battery_voltage,
                                "PV1/PV2 Voltage": row.pv1_pv2_voltage,
                                "PV1/PV2 Recharging Current": row.pv1_pv2_recharging_current,
                                "Total Charge Current": row.total_charge_current,
                                "AC Input Voltage/Frequency": row.ac_input_voltage_frequency,
                                "AC Output Voltage/Frequency": row.ac_output_voltage_frequency,
                            }
                            if isinstance(metrics, dict):
                                telemetry_metrics.update(metrics)

                            save_telemetry_reading(
                                provider="growatt",
                                device_key=dev_key,
                                update_time=update_time or _utc_now_iso(),
                                status=conn_status or "Online",
                                metrics=telemetry_metrics,
                                plant_id=str(idx + 1),
                            )
                        except Exception as supa_err:
                            log.warn(f"Supabase sync warning (Planta {plant_name}): {supa_err}")

                        # Actualizar estado de subida en tiempo real para las barras de la WebUI
                        _update_sync_status("growatt", idx + 1, len(plant_names), "syncing", base_dir)

                        try:
                            from providers.system_logger import log_sys_event
                            log_sys_event("INFO", "SCRAPER", f"[OK] Planta {idx+1}/{len(plant_names)}: {plant_name} | Tabla: {table_name}")
                        except Exception:
                            pass

                        results.append(
                            {
                                "index": idx,
                                "plant_name": plant_name,
                                "device_serial": device_serial or None,
                                "url": page.url,
                                "scraped_at": _utc_now_iso(),
                                "panel_device": {
                                    "connection_status": conn_status or None,
                                    "update_time": update_time or None,
                                },
                                "metrics": metrics,
                                "row": {
                                    "update_time": row.update_time,
                                    "connection_status": row.connection_status,
                                    "battery_voltage": row.battery_voltage,
                                    "pv1_pv2_voltage": row.pv1_pv2_voltage,
                                    "pv1_pv2_recharging_current": row.pv1_pv2_recharging_current,
                                    "total_charge_current": row.total_charge_current,
                                    "ac_input_voltage_frequency": row.ac_input_voltage_frequency,
                                    "ac_output_voltage_frequency": row.ac_output_voltage_frequency,
                                },
                            }
                        )
                    except Exception as e:
                        dump_debug(page, base_dir, f"growatt-dashboard-plant-error-{idx+1}")
                        log.warn(f"Planta falló: {type(e).__name__}: {e}")
                        continue

                payload = {
                    "generated_at": _utc_now_iso(),
                    "home_url": home_url,
                    "plants": plant_names,
                    "results": results,
                }
                _write_json(out_json, payload)
                log.ok(f"Guardado: {out_json}")
                _update_sync_status("growatt", len(plant_names), len(plant_names), "completed", base_dir)
            except Exception as e:
                try:
                    dump_debug(page, base_dir, "growatt-dashboard-exception")
                except Exception:
                    pass
                log.fail(f"Error inesperado: {type(e).__name__}: {e}")
                log.fail("Ver storage/growatt-dashboard-exception.url.txt/.html/.png")
                raise
            finally:
                context.close()
                browser.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

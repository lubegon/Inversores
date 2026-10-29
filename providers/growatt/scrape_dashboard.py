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
# Creado por Luis G.
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import RunLogger, dump_debug, env_flag, launch_browser
from .login import perform_login_on_page
from .voltage_sqlite import GrowattRow, connect_db, ensure_monitor_table, insert_monitor_row, monitor_table_name
from providers.supabase_client import save_device, save_plant, save_telemetry_reading, save_telemetry_readings_batch

DEFAULT_HOME_URL = "https://server.growatt.com/index"

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


def _is_login_screen(page) -> bool:
    """Detecta con precisión si la página actual está en el formulario de login.

    Evita falsos positivos con inputs ocultos de cambio de contraseña en /index.
    """
    try:
        current_url = (page.url or "").lower()
        if "/login" in current_url:
            return True
        acc = page.locator("#val_loginAccount").first
        if acc.count() > 0 and acc.is_visible():
            return True
    except Exception:
        pass
    return False


def _extract_panel_devices(page) -> list[tuple[str, str, str]]:
    """Extrae lista de (device_serial, connection_status, update_time) desde #tb_device_con.

    Soporta plantas con uno o múltiples inversores (.deviceBox).
    """
    devices: list[tuple[str, str, str]] = []
    tb = page.locator(SEL_TB_DEVICE)
    boxes = tb.locator(".deviceBox")
    count = 0
    try:
        count = boxes.count()
    except Exception:
        count = 0

    for idx in range(max(1, count)):
        box = boxes.nth(idx) if count > 0 else tb.locator(".deviceBox").first
        device_serial = ""
        try:
            device_serial = _s(box.locator("td.firstTd").first.inner_text())
        except Exception:
            device_serial = ""

        connection_status = ""
        try:
            td = box.locator("td").filter(has_text=re.compile(r"Connection\s+Status", re.I)).first
            connection_status = _s(td.locator("span span").first.inner_text())
            if not connection_status:
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

        if device_serial or connection_status or update_time:
            devices.append((device_serial, connection_status, update_time))

    return devices or [("", "", "")]


def _extract_panel_device(page) -> tuple[str, str, str]:
    """Compatibilidad: retorna el primer (device_serial, connection_status, update_time)."""
    return _extract_panel_devices(page)[0]


def _extract_metrics_from_embedded_table(page) -> dict[str, str]:
    """Lee las tablas de métricas embebidas o tooltips en la vista del dashboard Growatt.

    Incluye reintentos con hidratación activa de animPan3 si el AJAX tarda en responder.
    """
    out: dict[str, str] = {}

    for attempt in range(4):
        # 1. Asegurar hover/click sobre el ícono de batería para activar el tooltip dinámico
        for anim_sel in ["div.animPan.animPan3", ".animPan3", "div.animPan3", "div.animPan:nth-child(3)", "div.animPan"]:
            try:
                el = page.locator(anim_sel).first
                if el.count() > 0:
                    el.hover(timeout=1_500)
                    if attempt >= 2:
                        el.click(timeout=1_500, force=True)
                    page.wait_for_timeout(350)
                    break
            except Exception:
                pass

        # 2. Buscar en la tabla principal embebida (animPan3 i.tips.w table o similares)
        table_locators = [
            SEL_TIPS_BATTERY_TABLE,
            "div.animPan i.tips.w table",
            "i.tips.w table",
            "div[class*='animPan'] table",
            "#panel_device table",
            ".deviceBox table",
            "table:has(td:has-text('Battery Voltage'))",
            "table:has(td:has-text('Battery'))",
        ]

        table = None
        for loc in table_locators:
            try:
                t = page.locator(loc).filter(has_text=re.compile(r"(Battery|Voltage)", re.I)).first
                if t.count() > 0:
                    table = t
                    break
            except Exception:
                pass

        if table is not None:
            try:
                table.wait_for(state="attached", timeout=3_000)
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

        # 3. Fallback exhaustivo vía JS si aún no tenemos Battery Voltage
        has_battery = any("battery" in k.lower() for k in out.keys())
        if not has_battery:
            try:
                js_metrics = page.evaluate(
                    """() => {
                        const res = {};
                        const tables = Array.from(document.querySelectorAll('i.tips table, div.animPan table, .deviceBox table, table'));
                        for (const tbl of tables) {
                            const rows = Array.from(tbl.querySelectorAll('tr'));
                            for (const r of rows) {
                                const tds = r.querySelectorAll('td, th, span');
                                if (tds.length >= 2) {
                                    const k = (tds[0].textContent || tds[0].innerText || '').trim();
                                    const v = (tds[1].textContent || tds[1].innerText || '').trim();
                                    if (k && v && (k.toLowerCase().includes('battery') || k.toLowerCase().includes('voltage') || k.toLowerCase().includes('current') || k.toLowerCase().includes('charge') || k.toLowerCase().includes('ac input') || k.toLowerCase().includes('ac output'))) {
                                        res[k] = v.replace(/\\s+/g, '');
                                    }
                                }
                            }
                            if (Object.keys(res).some(k => k.toLowerCase().includes('battery'))) {
                                break;
                            }
                        }
                        return res;
                    }"""
                )
                if js_metrics and isinstance(js_metrics, dict):
                    for k, v in js_metrics.items():
                        if v:
                            out[k] = v
            except Exception:
                pass

        if any("battery" in k.lower() or "voltage" in k.lower() for k in out.keys()):
            break

        if attempt < 3:
            page.wait_for_timeout(1_200)

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
    for click_sel in [SEL_TOP_PLANT_TITLE, SEL_TOP_PLANT_SEARCH, "#top_plant_search", ".selectTitle"]:
        try:
            loc = page.locator(click_sel).first
            if loc.count() > 0:
                loc.click(timeout=3_000)
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

    # Opción B: Fallback vía JS en document.querySelectorAll (usando textContent por si está oculto)
    if not names:
        try:
            js_names = page.evaluate(
                """() => {
                    const els = Array.from(document.querySelectorAll('#header_sel_plantstwo dd, #header_sel_plantstwo li, .plant-name, .plant-title, div[data-plantname]'));
                    return els.map(e => (e.textContent || e.innerText || '').trim()).filter(Boolean);
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
        loc = page.locator(SEL_TOP_PLANT_TITLE).first
        if loc.count() > 0:
            loc.click(timeout=1_000)
    except Exception:
        pass

    return names


def _js_click_plant_dd(page, idx: int, plant_name: str = "") -> bool:
    try:
        return bool(
            page.evaluate(
                """
([idx, name]) => {
  const list = Array.from(document.querySelectorAll('#header_sel_plantstwo dd, #header_sel_plantstwo li'));
  let target = list[idx];
  if (name) {
    const byName = list.find(el => ((el.textContent || el.innerText || '').trim().toLowerCase() === name.trim().toLowerCase()) || ((el.textContent || '').toLowerCase().includes(name.trim().toLowerCase())));
    if (byName) target = byName;
  }
  if (target) {
    if (typeof target.click === 'function') {
      target.click();
    } else {
      target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }
    return true;
  }
  if (name) {
    const cards = Array.from(document.querySelectorAll('.plantItem, .plant-box, .deviceBox, dd, li'));
    const matched = cards.find(c => (c.textContent || c.innerText || '').includes(name));
    if (matched) {
      matched.click();
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
                loc = page.locator(click_sel).first
                if loc.count() > 0:
                    loc.click(timeout=2_000)
                    break
            except Exception:
                pass
        try:
            page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="attached", timeout=2_500)
            return
        except Exception:
            continue


def _select_plant_by_index(page, idx: int, plant_name: str = "") -> None:
    # 1) Abrir dropdown y hacer click real sobre el item si es posible
    try:
        _open_plants_dropdown(page)
        dd = page.locator(f"{SEL_TOP_PLANT_DD}, #header_sel_plantstwo dd").nth(idx)
        if dd.count() > 0 and dd.is_visible():
            dd.click(timeout=2_500)
            return
    except Exception:
        pass

    # 2) JS directo
    if _js_click_plant_dd(page, idx, plant_name):
        return

    # 3) Intentar click sobre tarjetas del Dashboard
    if plant_name:
        try:
            card = page.locator(f"text='{plant_name}'").first
            if card.count() > 0 and card.is_visible():
                card.click(timeout=2_500)
                return
        except Exception:
            pass


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    log = RunLogger(base_dir, log_filename="last_growatt_dashboard.log")

    storage_state_path = base_dir / "storage" / "growatt.json"
    headless = env_flag("HEADLESS", True)
    home_url = (os.getenv("GROWATT_HOME_URL") or DEFAULT_HOME_URL).strip()
    if home_url.rstrip("/") == "https://server.growatt.com" or "login" in home_url.lower():
        home_url = DEFAULT_HOME_URL

    limit_plants = os.getenv("GROWATT_LIMIT_PLANTS")
    limit_n = int(limit_plants) if limit_plants and limit_plants.isdigit() else None

    out_json = base_dir / "storage" / "growatt-dashboard.json"

    # SQLite Voltage Growatt
    conn = connect_db(base_dir)

    try:
        with sync_playwright() as p:
            log.step(f"Lanzando browser (headless={headless})")
            browser = launch_browser(p, headless=headless)
            if storage_state_path.exists():
                context = browser.new_context(storage_state=str(storage_state_path), ignore_https_errors=True)
            else:
                context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.set_default_timeout(25_000)
            page.set_default_navigation_timeout(45_000)
            # Permitir recursos visuales y diagramas para que los tooltips y animPan se rendericen
            try:
                page.route("**/*.{mp4,avi,mkv,webm}", lambda route: route.abort())
            except Exception:
                pass

            try:
                log.step("Abriendo home")
                page.goto(home_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(1_500)

                # Auto-recuperación de sesión expirada IN-PLACE (sin abrir un segundo sync_playwright)
                if _is_login_screen(page):
                    log.warn("Sesión expirada o en pantalla de login. Ejecutando login automático in-place...")
                    relog_ok = perform_login_on_page(page, context, base_dir, log)
                    if relog_ok and "index" not in (page.url or "").lower():
                        page.goto(home_url, wait_until="domcontentloaded", timeout=45_000)
                        page.wait_for_timeout(1_500)

                if _is_login_screen(page):
                    dump_debug(page, base_dir, "growatt-login-blocked")
                    log.fail("No se pudo iniciar sesión en Growatt (sigue en pantalla de login). Abortando captura.")
                    raise SystemExit(3)

                log.step("Esperando selector superior de plantas")
                try:
                    page.locator(f"{SEL_TOP_PLANT_SEARCH}, #header_sel_plantstwo, .selectTitle, #panel_device, #tb_device_con").first.wait_for(
                        state="attached", timeout=20_000
                    )
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
                pending_supabase_plants: list[dict[str, Any]] = []
                pending_supabase_devices: list[dict[str, Any]] = []
                pending_supabase_telemetry: list[dict[str, Any]] = []

                for idx, plant_name in enumerate(plant_names):
                    log.step(f"Planta {idx+1}/{len(plant_names)}: {plant_name}")
                    _update_sync_status("growatt", idx, len(plant_names), "syncing", base_dir)
                    try:
                        if _is_login_screen(page):
                            log.warn("Sesión expirada durante recorrido. Re-autenticando in-place...")
                            perform_login_on_page(page, context, base_dir, log)

                        _select_plant_by_index(page, idx, plant_name)

                        # Confirmar cambio de planta en el panel con timeout acotado (10s)
                        try:
                            plant_span = (
                                page.locator(SEL_TB_DEVICE)
                                .locator("td")
                                .filter(has_text=re.compile(r"Plant\s+Name", re.I))
                                .locator("span")
                                .first
                            )
                            plant_span.wait_for(state="attached", timeout=8_000)
                            page.wait_for_function(
                                "(el, expected) => (el && (el.innerText||'').toLowerCase().includes(expected.toLowerCase()))",
                                arg=(plant_span, plant_name),
                                timeout=8_000,
                            )
                        except Exception:
                            # Si el primer click no cambió la planta, reintentar vía JS directo
                            _js_click_plant_dd(page, idx, plant_name)

                        page.wait_for_timeout(1_800)

                        # Esperar panel del dispositivo (acotado a 12s en lugar de 120s)
                        try:
                            page.locator(f"{SEL_PANEL_DEVICE}, {SEL_TB_DEVICE}").first.wait_for(state="attached", timeout=12_000)
                        except Exception:
                            pass

                        # Extraer todos los dispositivos del panel (soporta plantas multi-inversor)
                        panel_devices = _extract_panel_devices(page)

                        # Tooltip: tabla embebida
                        metrics = _extract_metrics_from_embedded_table(page)
                        if not metrics:
                            log.warn("No encontré tabla de métricas (Battery Voltage) en animPan3")

                        for dev_serial, conn_status, update_time in panel_devices:
                            if not metrics and not dev_serial and not conn_status:
                                log.warn(
                                    f"Planta {idx+1}/{len(plant_names)}: {plant_name} no devolvió datos válidos. Omitiendo guardado de fila vacía."
                                )
                                continue

                            row = _row_from_metrics(
                                update_time=update_time,
                                connection_status=conn_status,
                                metrics=metrics,
                            )

                            # Persistir en SQLite (tabla por monitor/planta+serial)
                            monitor_name = plant_name
                            if dev_serial:
                                monitor_name = f"{plant_name}__{dev_serial}"

                            table_name = monitor_table_name(monitor_name)
                            ensure_monitor_table(conn, table_name=table_name)
                            insert_monitor_row(conn, table_name=table_name, row=row.as_list())
                            conn.commit()
                            log.ok(f"SQLite: insertado en {table_name} (Planta {idx+1}/{len(plant_names)})")

                            # Acumular registros para subida remota a Supabase al finalizar todo el scraping
                            dev_key = dev_serial or table_name
                            pending_supabase_plants.append({
                                "provider": "growatt",
                                "plant_id": str(idx + 1),
                                "name": plant_name,
                                "metadata": {"table_name": table_name, "device_serial": dev_serial},
                            })
                            pending_supabase_devices.append({
                                "provider": "growatt",
                                "plant_id": str(idx + 1),
                                "device_key": dev_key,
                                "device_name": monitor_name,
                                "device_type": "Inverter",
                                "metadata": {"table_name": table_name},
                            })
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
                                "device_serial": dev_serial,
                                "Battery Voltage": row.battery_voltage,
                                "PV1/PV2 Voltage": row.pv1_pv2_voltage,
                                "PV1/PV2 Recharging Current": row.pv1_pv2_recharging_current,
                                "Total Charge Current": row.total_charge_current,
                                "AC Input Voltage/Frequency": row.ac_input_voltage_frequency,
                                "AC Output Voltage/Frequency": row.ac_output_voltage_frequency,
                            }
                            if isinstance(metrics, dict):
                                telemetry_metrics.update(metrics)

                            pending_supabase_telemetry.append({
                                "provider": "growatt",
                                "device_key": dev_key,
                                "update_time": update_time or _utc_now_iso(),
                                "status": conn_status or "Online",
                                "metrics": telemetry_metrics,
                                "plant_id": str(idx + 1),
                            })

                            try:
                                from providers.system_logger import log_sys_event
                                log_sys_event("INFO", "SCRAPER", f"[OK] Planta {idx+1}/{len(plant_names)}: {plant_name} | Tabla: {table_name}")
                            except Exception:
                                pass

                            results.append(
                                {
                                    "index": idx,
                                    "plant_name": plant_name,
                                    "device_serial": dev_serial or None,
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
                log.ok(f"Guardado local: {out_json}")

                # 1) Envío en lote consolidado a Supabase ANTES de regenerar el reporte
                if pending_supabase_telemetry or pending_supabase_plants:
                    try:
                        log.step("Iniciando sincronización masiva con Supabase...")
                        for p_item in pending_supabase_plants:
                            save_plant(**p_item)
                        for d_item in pending_supabase_devices:
                            save_device(**d_item)
                        if pending_supabase_telemetry:
                            inserted = save_telemetry_readings_batch(pending_supabase_telemetry)
                            log.ok(f"Sincronización Supabase completada ({inserted} lecturas)")
                        _update_sync_status("growatt", len(plant_names), len(plant_names), "completed", base_dir)
                    except Exception as supa_err:
                        log.warn(f"Aviso en sincronización Supabase: {supa_err}")

                # 2) Actualizar reporte Excel local si se ejecuta de forma independiente (fuera de _waiter de WebUI)
                if not os.getenv("WEBUI_MANAGED_JOB"):
                    try:
                        from webui_server import _generate_or_update_report, _current_slot
                        curr_slot = _current_slot()
                        if curr_slot:
                            _generate_or_update_report(slot=curr_slot)
                            log.ok(f"Reporte Excel local actualizado automáticamente para el turno: {curr_slot}")
                    except Exception as ex:
                        log.warn(f"Aviso actualizando reporte Excel local: {ex}")
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

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
    """Lee la tabla embebida dentro de `i.tips.w` (sin hover).

    Retorna dict por etiqueta exacta (ej. 'Battery Voltage' -> '56.4V').
    """

    table = page.locator(SEL_TIPS_BATTERY_TABLE).filter(has_text=re.compile(r"Battery\s+Voltage", re.I)).first
    if table.count() == 0:
        return {}

    try:
        table.wait_for(state="attached", timeout=15_000)
    except Exception:
        return {}

    rows = table.locator("tbody tr")
    out: dict[str, str] = {}
    for i in range(rows.count()):
        tr = rows.nth(i)
        try:
            label = _s(tr.locator("td").nth(0).inner_text())
            value = _s(tr.locator("td").nth(1).inner_text())
        except Exception:
            continue
        if not label:
            continue
        if value:
            # Normaliza espacios (ej. '117.4V/60HZ' vs '117.4 V/60 HZ')
            value = re.sub(r"\s+", "", value)
        out[label] = value
    return out


def _row_from_metrics(*, update_time: str, connection_status: str, metrics: dict[str, str]) -> GrowattRow:
    def pick(*keys: str) -> str:
        for k in keys:
            for kk, vv in metrics.items():
                if kk.strip().lower() == k.strip().lower():
                    return vv
        return ""

    return GrowattRow(
        update_time=update_time,
        connection_status=connection_status,
        battery_voltage=pick("Battery Voltage"),
        pv1_pv2_voltage=pick("PV1/PV2 Voltage"),
        pv1_pv2_recharging_current=pick("PV1/PV2 Recharging Current"),
        total_charge_current=pick("Total Charge Current"),
        ac_input_voltage_frequency=pick("Ac Input Voltage/Frequency", "AC Input Voltage/Frequency"),
        ac_output_voltage_frequency=pick("AC Output Voltage/Frequency", "Ac Output Voltage/Frequency"),
    )


def _select_plants(page, log: RunLogger) -> list[str]:
    """Retorna nombres de plantas según el dropdown superior."""

    page.locator(SEL_TOP_PLANT_SEARCH).wait_for(state="attached", timeout=60_000)
    log.step("Abriendo dropdown de plantas")
    page.locator(SEL_TOP_PLANT_TITLE).click()
    page.locator(SEL_TOP_PLANT_DROPDOWN).wait_for(state="attached", timeout=60_000)
    page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="attached", timeout=60_000)

    dd = page.locator(SEL_TOP_PLANT_DD)
    names: list[str] = []
    for i in range(dd.count()):
        names.append((dd.nth(i).inner_text() or "").strip())

    # Dejar el dropdown cerrado para que el loop de selección no dependa del estado.
    try:
        page.locator(SEL_TOP_PLANT_TITLE).click(timeout=2_000)
    except Exception:
        pass
    return names


def _js_click_plant_dd(page, idx: int) -> bool:
    try:
        return bool(
            page.evaluate(
                """
(idx) => {
  const list = Array.from(document.querySelectorAll('#header_sel_plantstwo dd'));
  const el = list[idx];
  if (!el) return false;
  // Click robusto aunque el dropdown esté oculto.
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
  return true;
}
""",
                idx,
            )
        )
    except Exception:
        return False


def _open_plants_dropdown(page) -> None:
    # A veces el dropdown queda cerrado; reintentamos hasta ver un dd visible.
    for _ in range(3):
        try:
            page.locator(SEL_TOP_PLANT_TITLE).click(timeout=3_000)
        except Exception:
            try:
                page.locator(SEL_TOP_PLANT_TITLE).click(force=True, timeout=3_000)
            except Exception:
                pass

        try:
            page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="attached", timeout=5_000)
        except Exception:
            continue

        try:
            page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="visible", timeout=2_000)
            return
        except Exception:
            # Aunque no sea visible, igual podemos usar click force / JS.
            return


def _select_plant_by_index(page, idx: int) -> None:
    # 1) Intentar JS directo (no depende de visibilidad)
    if _js_click_plant_dd(page, idx):
        return

    # 2) Abrir dropdown y click normal/force
    _open_plants_dropdown(page)
    dd = page.locator(SEL_TOP_PLANT_DD).nth(idx)
    dd.wait_for(state="attached", timeout=20_000)
    try:
        dd.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    try:
        dd.click(timeout=5_000)
        return
    except Exception:
        dd.click(timeout=8_000, force=True)


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
                page.goto(home_url, wait_until="domcontentloaded", timeout=60_000)

                log.step("Esperando selector superior de plantas")
                page.locator(SEL_TOP_PLANT_SEARCH).wait_for(state="attached", timeout=60_000)

                plant_names = _select_plants(page, log)
                if limit_n is not None:
                    plant_names = plant_names[: max(0, limit_n)]
                log.ok(f"Plantas detectadas: {len(plant_names)}")

                results: list[dict[str, Any]] = []

                for idx, plant_name in enumerate(plant_names):
                    log.step(f"Planta {idx+1}/{len(plant_names)}: {plant_name}")
                    try:
                        _select_plant_by_index(page, idx)

                        # Confirmar cambio de planta en el panel (best-effort)
                        try:
                            plant_span = (
                                page.locator(SEL_TB_DEVICE)
                                .locator("td")
                                .filter(has_text=re.compile(r"Plant\\s+Name", re.I))
                                .locator("span")
                                .first
                            )
                            plant_span.wait_for(state="attached", timeout=15_000)
                            # Espera a que el texto contenga el nombre de la planta.
                            page.wait_for_function(
                                "(el, expected) => (el && (el.innerText||'').toLowerCase().includes(expected.toLowerCase()))",
                                arg=(plant_span, plant_name),
                                timeout=20_000,
                            )
                        except Exception:
                            pass

                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)

                        # Esperar panel del dispositivo.
                        try:
                            page.locator(SEL_PANEL_DEVICE).wait_for(state="attached", timeout=60_000)
                            page.locator(SEL_TB_DEVICE).wait_for(state="attached", timeout=60_000)
                        except PlaywrightTimeoutError:
                            dump_debug(page, base_dir, f"growatt-dashboard-no-panel-{idx+1}")
                            log.warn("No apareció #panel_device/#tb_device_con; guardé dump de debug")

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
                        log.ok(f"SQLite: insertado en {table_name}")

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

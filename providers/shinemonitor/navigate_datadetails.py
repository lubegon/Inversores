"""Navegación a *Data Details* por planta y monitor en ShineMonitor.

Este script es útil como herramienta de diagnóstico: recorre una planta,
expande el árbol (Inverter -> monitores), hace click monitor por monitor y abre
la pestaña *Data Details*, dejando capturas en `storage/nav/`.

Se mantiene como módulo (no script raíz) para evitar mezclar proveedores. El
wrapper compatible en la raíz sigue siendo:
- `python shinemonitor_navigate_datadetails.py`
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, sync_playwright

SHINE_URL = "https://shinemonitor.com/index_en.html?1770834820036"

SELECTOR_PLANTS_TOGGLE = "#headPlos > div.logo-container > div > a"
SELECTOR_PLANTS_LIST = "#plantlist > ul"

SELECTOR_DEVICE_MGMT_TAB = "#plantTab > li:nth-child(4) > a"  # Device Management
SELECTOR_TREE_BOX = "#treeLeftBox"  # contiene el árbol (Inverter, monitores, etc.)
SELECTOR_DATA_DETAILS_TAB = "#inverterpab > li:nth-child(5) > a"  # Data Details


@dataclass(frozen=True)
class RunLog:
    source: str
    captured_at: str
    plant_id: str
    plant_name: str | None
    inverter_present: bool
    devices_clicked: int
    notes: list[str]


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _browser_choice() -> str:
    return (os.getenv("BROWSER") or "chromium").strip().lower()


def _launch_browser(p, headless: bool):
    choice = _browser_choice()
    use_edge = choice in {"edge", "msedge"}

    try:
        if use_edge:
            return p.chromium.launch(headless=headless, channel="msedge")
        return p.chromium.launch(headless=headless)
    except PlaywrightError:
        if use_edge:
            return p.chromium.launch(headless=headless)
        raise


def _login_if_needed(page, user: str, password: str) -> None:
    if page.locator("#loginusr > input").is_visible():
        page.locator("#loginusr > input").fill(user)
        page.locator("#mypassword").fill(password)
        page.locator("#loginbtn").click()
        try:
            page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)


def _open_plants_dropdown(page) -> None:
    toggle = page.locator(SELECTOR_PLANTS_TOGGLE)
    toggle.wait_for(state="attached", timeout=30_000)

    for attempt in range(1, 4):
        try:
            toggle.hover(timeout=5_000)
        except Exception:
            pass

        try:
            toggle.click(timeout=10_000)
        except Exception:
            toggle.click(timeout=10_000, force=True)

        try:
            page.wait_for_selector("#plantlist", state="visible", timeout=10_000)
            break
        except Exception:
            if attempt == 3:
                raise

    page.wait_for_selector(SELECTOR_PLANTS_LIST, state="attached", timeout=30_000)
    page.wait_for_timeout(200)


def _select_plant(page, plant_id: str) -> str | None:
    _open_plants_dropdown(page)

    plant_anchor = page.locator(f"xpath=//*[@id='plant_{plant_id}']/a")
    plant_anchor.wait_for(state="visible", timeout=30_000)

    plant_name = " ".join(plant_anchor.inner_text().split()).strip() or None

    plant_anchor.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    page.wait_for_timeout(1200)
    return plant_name


def _click_device_management(page) -> None:
    tab = page.locator(SELECTOR_DEVICE_MGMT_TAB)
    tab.wait_for(state="visible", timeout=30_000)
    tab.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    page.wait_for_selector(SELECTOR_TREE_BOX, state="visible", timeout=30_000)
    page.wait_for_timeout(600)


def _ensure_tree_loaded(page) -> Locator:
    tree = page.locator(SELECTOR_TREE_BOX)
    tree.wait_for(state="visible", timeout=30_000)
    return tree


def _open_node_if_needed(node_li: Locator) -> None:
    klass = (node_li.get_attribute("class") or "").lower()
    if "jstree-open" in klass:
        return

    ocl = node_li.locator("xpath=./i[contains(@class,'jstree-ocl')]")
    if ocl.count() > 0:
        ocl.first.click()
        node_li.page.wait_for_timeout(300)


def _expand_tree(tree: Locator, *, rounds: int = 3, max_nodes_per_round: int = 50) -> None:
    for _ in range(max(1, rounds)):
        closed = tree.locator("li.jstree-closed")
        count = closed.count()
        if count == 0:
            return
        for i in range(min(count, max_nodes_per_round)):
            _open_node_if_needed(closed.nth(i))


def _collect_inverters_and_device_anchors(tree: Locator) -> tuple[int, list[Locator]]:
    _expand_tree(tree)

    inverter_anchors = tree.locator("a.jstree-anchor", has_text="Inverter")
    inv_count = inverter_anchors.count()
    if inv_count == 0:
        return 0, []

    results: list[Locator] = []
    seen: set[str] = set()
    for i in range(inv_count):
        inverter_li = inverter_anchors.nth(i).locator("xpath=ancestor::li[1]")
        _open_node_if_needed(inverter_li)
        for a in _collect_device_anchors_under(inverter_li):
            key = (a.get_attribute("id") or "").strip()
            if not key:
                key = "name:" + (
                    " ".join((a.inner_text() or "").split()).strip() or "unknown"
                )
            if key in seen:
                continue
            seen.add(key)
            results.append(a)
    return inv_count, results


def _collect_device_anchors_under(li: Locator) -> list[Locator]:
    anchors = li.locator("xpath=.//ul//a[contains(@class,'jstree-anchor')]")
    results: list[Locator] = []
    for i in range(anchors.count()):
        a = anchors.nth(i)
        text = " ".join(a.inner_text().split()).strip()
        if not text:
            continue
        if text.lower() == "inverter":
            continue
        results.append(a)
    return results


def _click_data_details(page) -> None:
    tab = page.locator(SELECTOR_DATA_DETAILS_TAB)
    if tab.count() == 0:
        tab = page.locator("#inverterpab a", has_text="Data Details")
    tab.wait_for(state="visible", timeout=30_000)
    tab.click()
    page.wait_for_timeout(700)


def _load_default_plant_id(storage_dir: Path) -> str | None:
    snapshot = storage_dir / "shinemonitor-plants.json"
    if not snapshot.exists():
        return None
    data: dict[str, Any] = json.loads(snapshot.read_text(encoding="utf-8"))
    plants = data.get("plants") or []
    if not plants:
        return None
    return str(plants[0].get("plant_id"))


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    user = os.getenv("SHINE_USER")
    password = os.getenv("SHINE_PASS")
    if not user or not password:
        raise SystemExit(
            "Faltan variables SHINE_USER/SHINE_PASS en .env (ver .env.example)."
        )

    headless = _env_flag("HEADLESS", True)

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage_state_path = storage_dir / "shinemonitor.json"

    plant_id = (os.getenv("PLANT_ID") or "").strip()
    if not plant_id:
        plant_id = _load_default_plant_id(storage_dir) or ""
    if not plant_id:
        raise SystemExit(
            "No se encontró PLANT_ID. Define PLANT_ID en .env o genera primero el snapshot con shinemonitor_discover_plants.py"
        )

    now = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    run_screenshot_dir = storage_dir / "nav"
    run_screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        if storage_state_path.exists():
            context = browser.new_context(storage_state=str(storage_state_path))
        else:
            context = browser.new_context()

        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        plant_name: str | None = None
        inverter_present = False
        devices_clicked = 0

        try:
            print(
                f"PLANT_ID={plant_id} headless={headless} browser={_browser_choice()}",
                flush=True,
            )
            page.goto(SHINE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("#loginusr > input", timeout=30_000)
            _login_if_needed(page, user=user, password=password)

            print("Seleccionando plant...", flush=True)
            plant_name = _select_plant(page, plant_id=plant_id)
            page.screenshot(
                path=str(run_screenshot_dir / f"{plant_id}-01-selected-plant.png"),
                full_page=True,
            )

            print("Abriendo Device Management...", flush=True)
            _click_device_management(page)
            page.screenshot(
                path=str(run_screenshot_dir / f"{plant_id}-02-device-management.png"),
                full_page=True,
            )

            print("Buscando Inverter en el árbol...", flush=True)
            tree = _ensure_tree_loaded(page)
            inverter_count, device_anchors = _collect_inverters_and_device_anchors(tree)

            if inverter_count == 0:
                notes.append("No se encontró el nodo 'Inverter' en el árbol")
                page.screenshot(
                    path=str(run_screenshot_dir / f"{plant_id}-03-no-inverter.png"),
                    full_page=True,
                )
            else:
                inverter_present = True
                if not device_anchors:
                    notes.append("El nodo Inverter no tiene monitores hijos")
                    page.screenshot(
                        path=str(run_screenshot_dir / f"{plant_id}-03-inverter-empty.png"),
                        full_page=True,
                    )
                else:
                    notes.append(f"Monitores bajo Inverter: {len(device_anchors)}")

                print(
                    f"Monitores encontrados bajo Inverter: {len(device_anchors)}",
                    flush=True,
                )

                # Importante: las locators pueden invalidarse; re-seleccionamos por índice cada vez.
                for idx in range(len(device_anchors)):
                    tree = _ensure_tree_loaded(page)
                    _, anchors = _collect_inverters_and_device_anchors(tree)
                    if idx >= len(anchors):
                        break

                    a = anchors[idx]
                    device_name = (
                        " ".join(a.inner_text().split()).strip() or f"device_{idx+1}"
                    )

                    print(
                        f"[{idx+1}/{len(device_anchors)}] Click monitor: {device_name}",
                        flush=True,
                    )
                    a.click()
                    page.wait_for_timeout(700)

                    print("Click Data Details...", flush=True)
                    _click_data_details(page)
                    devices_clicked += 1

                    safe_name = (
                        "".join(
                            ch
                            for ch in device_name
                            if ch.isalnum() or ch in {" ", "-", "_"}
                        )
                        .strip()
                        .replace(" ", "_")
                    )
                    page.screenshot(
                        path=str(
                            run_screenshot_dir
                            / f"{plant_id}-04-datadetails-{idx+1:02d}-{safe_name}.png"
                        ),
                        full_page=True,
                    )

            context.storage_state(path=str(storage_state_path))

        finally:
            context.close()
            browser.close()

    log = RunLog(
        source="shinemonitor",
        captured_at=now,
        plant_id=plant_id,
        plant_name=plant_name,
        inverter_present=inverter_present,
        devices_clicked=devices_clicked,
        notes=notes,
    )

    out_log_path = storage_dir / f"shinemonitor-nav-{plant_id}.json"
    out_log_path.write_text(
        json.dumps(log.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(log.__dict__, ensure_ascii=False, indent=2))
    print(f"Log: {out_log_path}")
    print(f"Screenshots: {run_screenshot_dir}")


if __name__ == "__main__":
    main()

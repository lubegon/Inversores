"""Descubrimiento de plantas (plants) en ShineMonitor.

Este módulo abre el dropdown de plantas y recorre el scroll para capturar la
lista completa, guardándola en `storage/shinemonitor-plants.json`.

Ejecución recomendada (wrapper en raíz):
- `python shinemonitor_discover_plants.py`
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SHINE_URL = "https://shinemonitor.com/index_en.html?1770834820036"

SELECTOR_PLANTS_TOGGLE = "#headPlos > div.logo-container > div > a"
SELECTOR_PLANTS_LIST = "#plantlist > ul"
SELECTOR_PLANT_ITEMS = "#plantlist > ul > li[id^='plant_'] > a"


@dataclass(frozen=True)
class Plant:
    plant_id: str
    name: str
    li_id: str


def _env_flag(name: str, default: bool) -> bool:
    """Lee booleanos desde env (0/false/no/off => False)."""

    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _browser_choice() -> str:
    return (os.getenv("BROWSER") or "chromium").strip().lower()


def _launch_browser(p, headless: bool):
    """Lanza navegador con fallback si el canal msedge no está disponible."""

    choice = _browser_choice()
    use_edge = choice in {"edge", "msedge"}

    try:
        if use_edge:
            return p.chromium.launch(headless=headless, channel="msedge")
        return p.chromium.launch(headless=headless)
    except PlaywrightError:
        if use_edge:
            # fallback a Chromium gestionado por Playwright
            return p.chromium.launch(headless=headless)
        raise


def _login_if_needed(page, user: str, password: str) -> None:
    # Si el campo de usuario está visible, asumimos que aún no hay sesión.
    if page.locator("#loginusr > input").is_visible():
        page.locator("#loginusr > input").fill(user)
        page.locator("#mypassword").fill(password)
        page.locator("#loginbtn").click()
        try:
            page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)


def _extract_plant(li_id: str, text: str) -> Plant | None:
    # li_id esperado: plant_214436
    match = re.match(r"^plant_(\d+)$", li_id)
    if not match:
        return None
    plant_id = match.group(1)
    name = " ".join(text.split()).strip()
    if not name:
        return None
    return Plant(plant_id=plant_id, name=name, li_id=li_id)


def discover_plants(page) -> list[Plant]:
    """Descubre todas las plantas visibles en el dropdown con scroll interno."""

    # Abrir dropdown con reintentos (a veces requiere hover o un click forzado)
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
            # en algunos layouts el click normal falla por overlay
            toggle.click(timeout=10_000, force=True)

        # Esperar a que el contenedor sea visible
        try:
            page.wait_for_selector("#plantlist", state="visible", timeout=10_000)
            break
        except Exception:
            if attempt == 3:
                raise

    page.wait_for_selector(SELECTOR_PLANTS_LIST, state="attached", timeout=30_000)
    page.wait_for_timeout(300)

    list_ul = page.locator(SELECTOR_PLANTS_LIST)

    seen: dict[str, Plant] = {}
    stable_rounds = 0

    # Recorre el scroll interno del dropdown hasta que no aparezcan nuevos items
    for _ in range(200):
        items = page.locator(SELECTOR_PLANT_ITEMS)
        count = items.count()
        for i in range(count):
            item = items.nth(i)
            li = item.locator("xpath=..")
            li_id = li.get_attribute("id") or ""
            text = item.inner_text()
            plant = _extract_plant(li_id=li_id, text=text)
            if plant and plant.plant_id not in seen:
                seen[plant.plant_id] = plant

        before = len(seen)

        # Scroll: mover al final del UL (si es scrollable)
        try:
            page.evaluate(
                """(el) => {
                    el.scrollTop = el.scrollTop + el.clientHeight;
                    return { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight };
                }""",
                list_ul,
            )
        except Exception:
            # Si no es scrollable, no hay más que hacer
            break

        page.wait_for_timeout(300)

        after = len(seen)
        if after == before:
            stable_rounds += 1
        else:
            stable_rounds = 0

        # si en varias rondas no aparecen nuevos, asumimos fin
        if stable_rounds >= 8:
            break

    return sorted(seen.values(), key=lambda p: int(p.plant_id))


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
    snapshot_path = storage_dir / "shinemonitor-plants.json"
    screenshot_path = storage_dir / "shinemonitor-plants.png"
    debug_open_menu_path = storage_dir / "debug-open-plants-menu.png"

    plants: list[Plant] = []

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)

        if storage_state_path.exists():
            context = browser.new_context(storage_state=str(storage_state_path))
        else:
            context = browser.new_context()

        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        try:
            page.goto(SHINE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("#loginusr > input", timeout=30_000)
            _login_if_needed(page, user=user, password=password)

            try:
                plants = discover_plants(page)
            except Exception:
                # Diagnóstico: capturar el estado de la pantalla si no se pudo abrir/encontrar el menú
                try:
                    page.screenshot(path=str(debug_open_menu_path), full_page=True)
                except Exception:
                    pass
                raise

            page.screenshot(path=str(screenshot_path), full_page=True)
            context.storage_state(path=str(storage_state_path))

            payload = {
                "source": "shinemonitor",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "count": len(plants),
                "plants": [asdict(p) for p in plants],
            }
            snapshot_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        finally:
            context.close()
            browser.close()

    print(f"Plants descubiertas: {len(plants)}")
    print(f"Snapshot: {snapshot_path}")
    print(f"Screenshot: {screenshot_path}")


if __name__ == "__main__":
    main()

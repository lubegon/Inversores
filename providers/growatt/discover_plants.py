"""Descubre plantas en Growatt (server.growatt.com).

Flujo (según portal actual):
1) En el home post-login existe un listado de plantas en `#selectPlant-con`.
2) Al entrar a una planta, el selector superior permite cambiar de planta:
   - abrir: `#top_plant_search > div.selectTitle`
   - items: `#header_sel_plantstwo > dd`

Salida:
- `storage/growatt-plants.json`
- Log paso a paso: `storage/last_growatt_discover.log`

Requisito:
- Haber ejecutado login antes para crear `storage/growatt.json`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .common import RunLogger, dump_debug, env_flag, launch_browser

DEFAULT_HOME_URL = "https://server.growatt.com/"

# Selectores relevantes (según lo que compartiste)
SEL_HOME_LIST = "#selectPlant-con"
SEL_HOME_LIST_ITEMS = "#selectPlant-con li[data-id]"
SEL_LOGIN_USER = "#val_loginAccount"
SEL_LOGIN_PASS = "#val_loginPwd"
SEL_LOGIN_SUBMIT = (
    "#body > div.loginTop.mylogin > div.loginContent.clearBox > "
    "div.loginBox.floatL > div > div.loginPro-box > div.loginBtn > button"
)
SEL_TOP_PLANT_SEARCH = "#top_plant_search"
SEL_TOP_PLANT_TITLE = "#top_plant_search > div.selectTitle"
SEL_TOP_PLANT_DROPDOWN = "#header_sel_plantstwo"
SEL_TOP_PLANT_DD = "#header_sel_plantstwo dd"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_home_plants(page) -> list[dict[str, Any]]:
    plants = []
    items = page.locator(SEL_HOME_LIST_ITEMS)
    count = items.count()
    for i in range(count):
        li = items.nth(i)
        plant_id = li.get_attribute("data-id")
        name = (li.locator("h4").inner_text() or "").strip()
        if not name:
            name = (li.inner_text() or "").strip()
        plants.append(
            {
                "id": plant_id,
                "name": " ".join(name.split()),
                "source": "home_list",
                "index": i,
            }
        )
    return plants


def _is_visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _login_if_needed(page, log: RunLogger) -> None:
    url = (page.url or "").lower()
    if "/login" not in url and not _is_visible(page.locator(SEL_LOGIN_USER)):
        return

    user = os.getenv("GROWATT_USER")
    password = os.getenv("GROWATT_PASS")
    if not user or not password:
        log.fail("Sesión expirada y faltan GROWATT_USER/GROWATT_PASS en .env")
        raise SystemExit(4)

    log.step("Sesión expirada: haciendo login")
    page.locator(SEL_LOGIN_USER).wait_for(state="visible", timeout=60_000)
    page.locator(SEL_LOGIN_PASS).wait_for(state="visible", timeout=60_000)
    page.locator(SEL_LOGIN_USER).fill(user)
    page.locator(SEL_LOGIN_PASS).fill(password)
    page.locator(SEL_LOGIN_SUBMIT).click()
    try:
        page.wait_for_load_state("networkidle", timeout=60_000)
    except Exception:
        pass


def _wait_for_plants_ui(page, log: RunLogger, *, timeout_ms: int = 60_000) -> str:
    """Espera hasta que exista la UI de lista de plantas o el selector superior.

    Retorna:
    - "home_list" si aparece la lista del home
    - "top_selector" si aparece el selector superior de plantas
    """

    started = time.time()
    while (time.time() - started) * 1000 < timeout_ms:
        _login_if_needed(page, log)

        if page.locator(SEL_HOME_LIST_ITEMS).count() > 0:
            return "home_list"
        if page.locator(SEL_TOP_PLANT_SEARCH).count() > 0:
            return "top_selector"
        page.wait_for_timeout(250)

    return "timeout"


def _extract_dropdown_plants(page) -> list[dict[str, Any]]:
    # Muchos tenants renderizan dd sin data-id; igual guardamos nombre.
    dd = page.locator(SEL_TOP_PLANT_DD)
    count = dd.count()
    result: list[dict[str, Any]] = []
    for i in range(count):
        node = dd.nth(i)
        plant_id = (
            node.get_attribute("data-id")
            or node.get_attribute("data-plantid")
            or node.get_attribute("data-plantId")
        )
        name = (node.inner_text() or "").strip()
        result.append(
            {
                "id": plant_id,
                "name": " ".join(name.split()),
                "source": "top_dropdown",
                "index": i,
            }
        )
    return result


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    log = RunLogger(base_dir, log_filename="last_growatt_discover.log")

    storage_state_path = base_dir / "storage" / "growatt.json"
    if not storage_state_path.exists():
        log.fail("No existe storage/growatt.json. Ejecuta primero growatt_login.py")
        raise SystemExit(2)

    headless = env_flag("HEADLESS", True)
    out_json = base_dir / "storage" / "growatt-plants.json"
    home_url = (os.getenv("GROWATT_HOME_URL") or DEFAULT_HOME_URL).strip()

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

            log.step("Esperando UI de plantas (home list o selector superior)")
            mode = _wait_for_plants_ui(page, log, timeout_ms=60_000)
            if mode == "timeout":
                dump_debug(page, base_dir, "growatt-discover-timeout")
                log.fail("Timeout esperando UI de plantas; ver storage/growatt-discover-timeout.*")
                raise SystemExit(3)

            home_plants: list[dict[str, Any]] = []
            if mode == "home_list":
                home_plants = _extract_home_plants(page)
                log.ok(f"Plantas en home: {len(home_plants)}")

                if home_plants:
                    # Entrar a la primera planta para habilitar el selector superior.
                    log.step("Entrando a la primera planta")
                    page.locator(SEL_HOME_LIST_ITEMS).first.click()

                    log.step("Esperando selector superior (#top_plant_search)")
                    page.locator(SEL_TOP_PLANT_SEARCH).wait_for(
                        state="attached", timeout=60_000
                    )
            else:
                log.ok("Ya estás dentro de una planta (selector superior disponible)")

            # Abrir dropdown.
            log.step("Abriendo dropdown de plantas")
            page.locator(SEL_TOP_PLANT_TITLE).click()
            page.locator(SEL_TOP_PLANT_DROPDOWN).wait_for(state="attached", timeout=60_000)
            page.locator(SEL_TOP_PLANT_DD).first.wait_for(state="attached", timeout=60_000)

            dropdown_plants = _extract_dropdown_plants(page)
            log.ok(f"Plantas en dropdown: {len(dropdown_plants)}")

            visited: list[dict[str, Any]] = []
            # Recorremos cada planta (solo navegar, sin extraer tablas aún).
            for i in range(len(dropdown_plants)):
                # Re-abrir cada vez (al hacer click suele cerrarse).
                try:
                    page.locator(SEL_TOP_PLANT_TITLE).click(timeout=5_000)
                except Exception:
                    page.locator(SEL_TOP_PLANT_TITLE).click()

                dd = page.locator(SEL_TOP_PLANT_DD).nth(i)
                name = (dd.inner_text() or "").strip()
                log.step(f"Seleccionando planta {i+1}/{len(dropdown_plants)}: {name}")

                dd.click()
                # Espera corta para permitir que el frontend cambie de planta.
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                page.wait_for_timeout(800)

                visited.append(
                    {
                        "index": i,
                        "name": " ".join(name.split()),
                        "id": dropdown_plants[i].get("id"),
                        "url": page.url,
                        "visited_at": _utc_now_iso(),
                    }
                )

            payload = {
                "generated_at": _utc_now_iso(),
                "home_url": home_url,
                "home_plants": home_plants,
                "dropdown_plants": dropdown_plants,
                "visited": visited,
            }
            _write_json(out_json, payload)
            log.ok(f"Guardado: {out_json}")
        except SystemExit:
            raise
        except Exception:
            dump_debug(page, base_dir, "growatt-discover-exception")
            log.fail("Fallo inesperado; ver storage/growatt-discover-exception.*")
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()

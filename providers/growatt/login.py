"""Login de Growatt (server.growatt.com).

Guarda un `storageState` reutilizable en `storage/growatt.json`.

Requisitos `.env`:
- `GROWATT_USER`, `GROWATT_PASS`

Opcional:
- `HEADLESS` (default true)
- `BROWSER` (chromium/msedge)
- `GROWATT_LOGIN_URL` (default https://server.growatt.com/login)
- `GROWATT_SEL_USER`, `GROWATT_SEL_PASS`, `GROWATT_SEL_SUBMIT`
- `GROWATT_SEL_HOME_READY` (selector que garantice sesión iniciada)

Ejecución recomendada (wrapper en raíz):
- `python growatt_login.py`
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .common import RunLogger, env_flag, launch_browser

DEFAULT_LOGIN_URL = "https://server.growatt.com/login"
DEFAULT_SEL_USER = "#val_loginAccount"
DEFAULT_SEL_PASS = "#val_loginPwd"
DEFAULT_SEL_SUBMIT = (
    "#body > div.loginTop.mylogin > div.loginContent.clearBox > "
    "div.loginBox.floatL > div > div.loginPro-box > div.loginBtn > button"
)


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    log = RunLogger(base_dir)
    log.step("Cargando .env")
    load_dotenv(dotenv_path=base_dir / ".env")
    # Permite aislar credenciales de Growatt sin tocar el .env general.
    growatt_dotenv = base_dir / ".env.growatt"
    if growatt_dotenv.exists():
        log.step("Cargando .env.growatt (override)")
        load_dotenv(dotenv_path=growatt_dotenv, override=True)

    user = os.getenv("GROWATT_USER")
    password = os.getenv("GROWATT_PASS")
    if not user:
        try:
            user = input("GROWATT_USER: ").strip()
        except Exception:
            user = None
    if not password:
        try:
            password = getpass.getpass("GROWATT_PASS (no se mostrará): ")
        except Exception:
            password = None

    if not user or not password:
        log.fail(
            "Faltan credenciales. Define GROWATT_USER/GROWATT_PASS en .env, "
            "o ingrésalas cuando el script las solicite."
        )
        raise SystemExit(2)

    login_url = (os.getenv("GROWATT_LOGIN_URL") or DEFAULT_LOGIN_URL).strip()
    sel_user = (os.getenv("GROWATT_SEL_USER") or DEFAULT_SEL_USER).strip()
    sel_pass = (os.getenv("GROWATT_SEL_PASS") or DEFAULT_SEL_PASS).strip()
    sel_submit = (os.getenv("GROWATT_SEL_SUBMIT") or DEFAULT_SEL_SUBMIT).strip()
    sel_home_ready = (os.getenv("GROWATT_SEL_HOME_READY") or "").strip() or None

    headless = env_flag("HEADLESS", True)

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage_state_path = storage_dir / "growatt.json"
    screenshot_path = storage_dir / "growatt-after-login.png"

    with sync_playwright() as p:
        log.step(f"Lanzando browser (headless={headless})")
        browser = launch_browser(p, headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        try:
            log.step("Abriendo URL de login")
            page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

            log.step("Esperando campos de login")
            page.locator(sel_user).wait_for(state="visible", timeout=60_000)
            page.locator(sel_pass).wait_for(state="visible", timeout=60_000)

            log.step("Rellenando credenciales")
            page.locator(sel_user).fill(user)
            page.locator(sel_pass).fill(password)

            log.step("Enviando login")
            page.locator(sel_submit).click()
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception:
                pass

            if sel_home_ready:
                log.step("Esperando selector de home (GROWATT_SEL_HOME_READY)")
                page.locator(sel_home_ready).wait_for(state="visible", timeout=60_000)
            else:
                # Fallback: Growatt suele quedarse como SPA; esperamos salir de /login.
                log.step("Esperando salir de /login")
                try:
                    page.wait_for_function(
                        "() => !String(location.pathname || '').includes('login')",
                        timeout=60_000,
                    )
                except Exception:
                    # Último recurso: pequeño delay para que el frontend navegue.
                    page.wait_for_timeout(3000)

            login_still_visible = False
            try:
                login_still_visible = page.locator(sel_user).is_visible()
            except Exception:
                login_still_visible = False

            if login_still_visible and "/login" in (page.url or ""):
                log.fail(
                    "Parece que el login no avanzó (sigue visible el formulario). "
                    "Revisa storage/growatt-after-login.png para ver el motivo. "
                    "Tip: prueba HEADLESS=false si aparece captcha/popup."
                )
                raise SystemExit(3)

            log.step("Guardando screenshot + storageState")
            page.screenshot(path=str(screenshot_path), full_page=True)
            context.storage_state(path=str(storage_state_path))
            log.ok("Login OK")
            log.ok(f"Screenshot: {screenshot_path}")
            log.ok(f"Storage state: {storage_state_path}")
        except Exception:
            log.fail("Excepción durante login; guardando screenshot")
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()

    # (Logs ya fueron escritos por RunLogger)


if __name__ == "__main__":
    main()

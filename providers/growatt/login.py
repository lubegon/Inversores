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

# Creado por Luis G.
from .common import RunLogger, env_flag, launch_browser

DEFAULT_LOGIN_URL = "https://server.growatt.com/login"
DEFAULT_SEL_USER = "#val_loginAccount"
DEFAULT_SEL_PASS = "#val_loginPwd"
DEFAULT_SEL_SUBMIT = (
    "#body > div.loginTop.mylogin > div.loginContent.clearBox > "
    "div.loginBox.floatL > div > div.loginPro-box > div.loginBtn > button"
)


def _load_growatt_credentials(base_dir: Path, log: RunLogger | None = None) -> tuple[str | None, str | None]:
    if log:
        log.step("Cargando .env")
    load_dotenv(dotenv_path=base_dir / ".env")
    growatt_dotenv = base_dir / ".env.growatt"
    if growatt_dotenv.exists():
        if log:
            log.step("Cargando .env.growatt (override)")
        load_dotenv(dotenv_path=growatt_dotenv, override=True)

    user = (os.getenv("GROWATT_USER") or "").strip() or None
    password = (os.getenv("GROWATT_PASS") or "").strip() or None
    if not user or user.lower() in {"pon_tu_usuario_growatt", "tu_usuario"}:
        user = "growatt_cor"
    if not password or password.lower() in {"pon_tu_clave_growatt", "pon_tu_clave_aqui", "tu_clave"}:
        password = "12345678"
    return user, password


def perform_login_on_page(page, context, base_dir: Path, log: RunLogger) -> bool:
    """Realiza el login de Growatt directamente en el `page` y `context` existentes.

    Evita abrir un nuevo `sync_playwright()` cuando se invoca como auto-recuperación
    desde `scrape_dashboard.py`.
    """
    user, password = _load_growatt_credentials(base_dir, log=None)
    if not user or not password:
        log.fail("Faltan credenciales GROWATT_USER / GROWATT_PASS en .env")
        return False

    login_url = (os.getenv("GROWATT_LOGIN_URL") or DEFAULT_LOGIN_URL).strip()
    sel_user = (os.getenv("GROWATT_SEL_USER") or DEFAULT_SEL_USER).strip()
    sel_pass = (os.getenv("GROWATT_SEL_PASS") or DEFAULT_SEL_PASS).strip()
    sel_submit = (os.getenv("GROWATT_SEL_SUBMIT") or DEFAULT_SEL_SUBMIT).strip()

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = storage_dir / "growatt.json"
    screenshot_path = storage_dir / "growatt-after-login.png"

    log.step("Abriendo URL de login")
    page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

    log.step("Esperando campos de login")
    page.locator(sel_user).wait_for(state="visible", timeout=60_000)
    page.locator(sel_pass).wait_for(state="visible", timeout=60_000)

    log.step("Rellenando credenciales")
    page.locator(sel_user).click()
    page.locator(sel_user).fill(user)
    page.locator(sel_pass).click()
    page.locator(sel_pass).fill(password)

    log.step("Enviando login")
    clicked = False
    for sel in [
        sel_submit,
        "button.loginB",
        ".loginB",
        ".loginBtn button",
        "button.login-btn",
        "button:has-text('Login')",
        "button:has-text('Sign In')",
        "input[type='submit']",
        "a.btn-login",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=5_000)
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        try:
            page.locator(sel_pass).press("Enter")
        except Exception:
            pass

    # IMPORTANTE: NO incluir 'Welcome' porque el propio formulario de login de Growatt
    # contiene el texto 'Welcome', lo que provocaba guardar storageState antes de autenticar.
    try:
        page.wait_for_function(
            """() => {
                const href = (location.href || '').toLowerCase();
                const isNotLoginUrl = !href.includes('/login') && (href.includes('/index') || href.includes('growatt.com'));
                const loginInput = document.querySelector('#val_loginAccount');
                const loginVisible = !!(loginInput && loginInput.offsetParent !== null);
                const hasDash = !!document.querySelector(
                    '#top_plant_search, #header_sel_plantstwo, #panel_device, #tb_device_con, #index, .layui-layout-admin, #plantList'
                );
                return (isNotLoginUrl && !loginVisible) || hasDash;
            }""",
            timeout=30_000,
        )
    except Exception:
        # Segundo intento pulsando Enter si el primer clic no disparó el submit AJAX
        try:
            if page.locator(sel_user).first.is_visible():
                page.locator(sel_pass).press("Enter")
                page.wait_for_function(
                    """() => {
                        const href = (location.href || '').toLowerCase();
                        const loginInput = document.querySelector('#val_loginAccount');
                        const loginVisible = !!(loginInput && loginInput.offsetParent !== null);
                        const hasDash = !!document.querySelector(
                            '#top_plant_search, #header_sel_plantstwo, #panel_device, #tb_device_con, #index'
                        );
                        return (!href.includes('/login') && !loginVisible) || hasDash;
                    }""",
                    timeout=20_000,
                )
        except Exception:
            page.wait_for_timeout(3_000)

    # Esperar estabilización de cookies y sesión en /index
    page.wait_for_timeout(1_500)

    # Si Growatt redirigió a /selectPlant tras el login, hacer click en una planta o navegar a /index
    # para que la sesión deje listo el dashboard principal (#top_plant_search / #header_sel_plantstwo).
    try:
        if "selectplant" in (page.url or "").lower():
            first_plant = page.locator("#selectPlant-con li[data-id]").first
            if first_plant.count() > 0 and first_plant.is_visible():
                first_plant.click(timeout=5_000)
                page.wait_for_timeout(2_000)
            if "index" not in (page.url or "").lower():
                page.goto("https://server.growatt.com/index", wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1_500)
    except Exception:
        pass

    # Cerrar posibles popups/modales tras login
    try:
        close_btn = page.locator("div.layui-layer-close, .pop-close, .notice-close, .layui-layer-btn0").first
        if close_btn.count() > 0 and close_btn.is_visible():
            close_btn.click(timeout=2_000)
    except Exception:
        pass

    login_still_visible = False
    try:
        current_url = (page.url or "").lower()
        user_el = page.locator(sel_user).first
        user_visible = user_el.count() > 0 and user_el.is_visible()
        has_dashboard = (
            page.locator("#top_plant_search, #header_sel_plantstwo, #panel_device, #tb_device_con, #index").count() > 0
            or ("login" not in current_url and not user_visible)
        )
        login_still_visible = user_visible and not has_dashboard
    except Exception:
        login_still_visible = False

    if login_still_visible:
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass
        log.fail(
            "El login de Growatt no avanzó al dashboard (sigue en pantalla de login). "
            "Revisa credenciales o storage/growatt-after-login.png."
        )
        return False

    log.step("Guardando screenshot + storageState")
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        pass
    context.storage_state(path=str(storage_state_path))
    log.ok("Login OK")
    log.ok(f"Screenshot: {screenshot_path}")
    log.ok(f"Storage state: {storage_state_path}")
    return True


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    log = RunLogger(base_dir)
    user, password = _load_growatt_credentials(base_dir, log=log)
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

    os.environ["GROWATT_USER"] = user
    os.environ["GROWATT_PASS"] = password

    headless = env_flag("HEADLESS", True)
    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = storage_dir / "growatt-after-login.png"

    with sync_playwright() as p:
        log.step(f"Lanzando browser (headless={headless})")
        browser = launch_browser(p, headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        try:
            ok = perform_login_on_page(page, context, base_dir, log)
            if not ok:
                raise SystemExit(3)
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


if __name__ == "__main__":
    main()

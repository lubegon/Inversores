"""Login de ShineMonitor.

Este módulo contiene la lógica para iniciar sesión en shinemonitor.com y
persistir el `storageState` para reutilizar la sesión en ejecuciones futuras.

Se mantiene separado del script raíz para:
- Tener una estructura por proveedor (orden del repo).
- Facilitar integración posterior con un orquestador/UI web.

Ejecución recomendada (wrapper en raíz):
- `python shinemonitor_login.py`
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SHINE_URL = "https://shinemonitor.com/index_en.html?1770834820036"


def _env_flag(name: str, default: bool) -> bool:
    """Lee booleanos desde env.

    Acepta valores falsy típicos: 0/false/no/off (case-insensitive).
    """

    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def main() -> None:
    # Importante: al estar dentro de providers/, calculamos el root del proyecto.
    # providers/shinemonitor/login.py -> parents[2] = <repo-root>
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
    screenshot_path = storage_dir / "shinemonitor-after-login.png"

    with sync_playwright() as p:
        # Nota: Edge (channel="msedge") puede fallar en algunos equipos/políticas.
        # Por defecto usamos Chromium gestionado por Playwright (más estable).
        browser_choice = (os.getenv("BROWSER") or "chromium").strip().lower()
        use_edge = browser_choice in {"edge", "msedge"}

        print(
            f"Browser: {'msedge' if use_edge else 'chromium'} | headless={headless}",
            flush=True,
        )

        try:
            if use_edge:
                browser = p.chromium.launch(headless=headless, channel="msedge")
            else:
                browser = p.chromium.launch(headless=headless)
        except PlaywrightError:
            if use_edge:
                browser = p.chromium.launch(headless=headless)
            else:
                raise

        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        try:
            print("Goto login...", flush=True)
            page.goto(SHINE_URL, wait_until="domcontentloaded", timeout=60_000)

            print("Waiting for login fields...", flush=True)
            page.wait_for_selector("#loginusr > input", timeout=30_000)
            page.wait_for_selector("#mypassword", timeout=30_000)
            page.wait_for_selector("#loginbtn", timeout=30_000)

            print("Filling credentials...", flush=True)
            page.locator("#loginusr > input").fill(user)
            page.locator("#mypassword").fill(password)

            print("Click login...", flush=True)
            page.locator("#loginbtn").click()
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception:
                pass

            page.wait_for_timeout(2000)

            login_visible = False
            try:
                login_visible = page.locator("#loginusr > input").is_visible()
            except Exception:
                login_visible = False

            print("Saving screenshot + storageState...", flush=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            context.storage_state(path=str(storage_state_path))

            if login_visible and "index_en.html" in (page.url or ""):
                raise SystemExit(
                    "Parece que el login no avanzó (sigue visible el formulario). "
                    "Revisa storage/shinemonitor-after-login.png para ver el motivo."
                )
        except Exception:
            # Diagnóstico mínimo: si algo falla, guardamos screenshot para inspección.
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()

    print("Login OK")
    print(f"Screenshot: {screenshot_path}")
    print(f"Storage state: {storage_state_path}")


if __name__ == "__main__":
    main()

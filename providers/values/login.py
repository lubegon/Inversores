"""Login Values.

Este módulo inicia sesión y guarda un `storageState` reutilizable.

Requisitos `.env`:
- `VALUES_USER`, `VALUES_PASS`
- `VALUES_LOGIN_URL`
- `VALUES_SEL_USER`, `VALUES_SEL_PASS`, `VALUES_SEL_SUBMIT`

Opcional:
- `VALUES_SEL_HOME_READY` (selector que garantice que ya estás logueado)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .common import env_flag, launch_browser


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    user = os.getenv("VALUES_USER")
    password = os.getenv("VALUES_PASS")
    login_url = os.getenv("VALUES_LOGIN_URL")
    sel_user = os.getenv("VALUES_SEL_USER")
    sel_pass = os.getenv("VALUES_SEL_PASS")
    sel_submit = os.getenv("VALUES_SEL_SUBMIT")
    sel_home_ready = os.getenv("VALUES_SEL_HOME_READY")

    missing = [
        name
        for name, val in {
            "VALUES_USER": user,
            "VALUES_PASS": password,
            "VALUES_LOGIN_URL": login_url,
            "VALUES_SEL_USER": sel_user,
            "VALUES_SEL_PASS": sel_pass,
            "VALUES_SEL_SUBMIT": sel_submit,
        }.items()
        if not val
    ]
    if missing:
        raise SystemExit(
            "Faltan variables en .env: " + ", ".join(missing) + ". "
            "Pásame URLs/selectores y lo dejamos fijo."
        )

    headless = env_flag("HEADLESS", True)

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = storage_dir / "values.json"
    screenshot_path = storage_dir / "values-after-login.png"

    with sync_playwright() as p:
        browser = launch_browser(p, headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        try:
            print(f"Browser={os.getenv('BROWSER') or 'chromium'} headless={headless}")
            print("Goto login...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

            print("Filling credentials...")
            page.locator(sel_user).wait_for(state="visible", timeout=30_000)
            page.locator(sel_pass).wait_for(state="visible", timeout=30_000)

            page.locator(sel_user).fill(user)
            page.locator(sel_pass).fill(password)

            print("Submit login...")
            page.locator(sel_submit).click()
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception:
                pass

            if sel_home_ready:
                print("Waiting for home ready selector...")
                page.locator(sel_home_ready).wait_for(state="visible", timeout=60_000)
            else:
                # Fallback: damos tiempo para que el frontend navegue.
                page.wait_for_timeout(3000)

            print("Saving screenshot + storageState...")
            page.screenshot(path=str(screenshot_path), full_page=True)
            context.storage_state(path=str(storage_state_path))
        except Exception:
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

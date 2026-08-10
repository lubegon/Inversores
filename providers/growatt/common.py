from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError


class RunLogger:
    def __init__(self, base_dir: Path, *, log_filename: str = "last_growatt_login.log") -> None:
        self.base_dir = base_dir
        self.log_path = base_dir / "storage" / log_filename
        # En Windows, para correr como background sin consola, desactiva stdout:
        # GROWATT_LOG_STDOUT=0
        self.print_to_stdout = str(os.getenv("GROWATT_LOG_STDOUT", "1")).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._step = 0

    def _write(self, line: str, level: str = "INFO") -> None:
        if self.print_to_stdout:
            print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            from providers.system_logger import log_sys_event
            log_sys_event(level, "GROWATT", line.strip())
        except Exception:
            pass

    def step(self, text: str) -> None:
        self._step += 1
        self._write(f"[{self._step:02d}] {text}", level="INFO")

    def ok(self, text: str) -> None:
        self._write(f"     OK: {text}", level="INFO")

    def warn(self, text: str) -> None:
        self._write(f"   WARN: {text}", level="WARNING")

    def fail(self, text: str) -> None:
        self._write(f"   FAIL: {text}", level="ERROR")


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def browser_choice() -> str:
    return (os.getenv("BROWSER") or "chromium").strip().lower()


FIREWALL_SAFE_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-zygote",
]


def launch_browser(p, *, headless: bool):
    choice = browser_choice()
    use_edge = choice in {"edge", "msedge"}

    try:
        if use_edge:
            return p.chromium.launch(headless=headless, channel="msedge", args=FIREWALL_SAFE_ARGS)
        return p.chromium.launch(headless=headless, args=FIREWALL_SAFE_ARGS)
    except PlaywrightError:
        if use_edge:
            return p.chromium.launch(headless=headless, args=FIREWALL_SAFE_ARGS)
        raise


def dump_debug(page, base_dir: Path, name: str) -> None:
    """Guarda url/html/screenshot para diagnóstico en storage/."""

    run_dir = base_dir / "storage"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    try:
        (run_dir / f"{name}.url.txt").write_text(page.url or "", encoding="utf-8")
    except Exception:
        pass
    try:
        (run_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(run_dir / f"{name}.png"), full_page=True)
    except Exception:
        pass


def dump_debug(page, base_dir: Path, name: str) -> None:
    run_dir = base_dir / "storage"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    try:
        (run_dir / f"{name}.url.txt").write_text(page.url or "", encoding="utf-8")
    except Exception:
        pass
    try:
        (run_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(run_dir / f"{name}.png"), full_page=True)
    except Exception:
        pass

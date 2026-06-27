from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError


class RunLogger:
    def __init__(self, base_dir: Path, *, log_filename: str = "last_values_run.log") -> None:
        self.base_dir = base_dir
        self.log_path = base_dir / "storage" / log_filename
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._step = 0

    def _write(self, line: str) -> None:
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def step(self, text: str) -> None:
        self._step += 1
        self._write(f"[{self._step:02d}] {text}")

    def ok(self, text: str) -> None:
        self._write(f"     OK: {text}")

    def warn(self, text: str) -> None:
        self._write(f"   WARN: {text}")

    def fail(self, text: str) -> None:
        self._write(f"   FAIL: {text}")


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def browser_choice() -> str:
    return (os.getenv("BROWSER") or "chromium").strip().lower()


def launch_browser(p, *, headless: bool):
    choice = browser_choice()
    use_edge = choice in {"edge", "msedge"}

    try:
        if use_edge:
            return p.chromium.launch(headless=headless, channel="msedge")
        return p.chromium.launch(headless=headless)
    except PlaywrightError:
        if use_edge:
            return p.chromium.launch(headless=headless)
        raise


def dump_debug(page, run_dir: Path, name: str) -> None:
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


def values_login_if_needed(page) -> None:
    """Hace login si el sitio redirige a la pantalla de entrada.

    Usa `.env`:
    - VALUES_USER / VALUES_PASS
    - VALUES_SEL_USER / VALUES_SEL_PASS / VALUES_SEL_SUBMIT
    - (opcional) VALUES_SEL_HOME_READY
    """

    url = (page.url or "").lower()
    needs = "#/entry" in url or "entry?redirect" in url
    if not needs:
        # Best-effort: si el form está visible, también consideramos que falta login.
        sel_user = os.getenv("VALUES_SEL_USER") or ""
        try:
            if sel_user and page.locator(sel_user).count() > 0 and page.locator(sel_user).first.is_visible():
                needs = True
        except Exception:
            pass

    if not needs:
        return

    user = os.getenv("VALUES_USER")
    password = os.getenv("VALUES_PASS")
    sel_user = os.getenv("VALUES_SEL_USER")
    sel_pass = os.getenv("VALUES_SEL_PASS")
    sel_submit = os.getenv("VALUES_SEL_SUBMIT")
    sel_home_ready = os.getenv("VALUES_SEL_HOME_READY")

    missing = [
        name
        for name, val in {
            "VALUES_USER": user,
            "VALUES_PASS": password,
            "VALUES_SEL_USER": sel_user,
            "VALUES_SEL_PASS": sel_pass,
            "VALUES_SEL_SUBMIT": sel_submit,
        }.items()
        if not val
    ]
    if missing:
        raise RuntimeError("No se puede auto-login; faltan en .env: " + ", ".join(missing))

    page.locator(sel_user).wait_for(state="visible", timeout=60_000)
    page.locator(sel_pass).wait_for(state="visible", timeout=60_000)
    page.locator(sel_user).fill(user)
    page.locator(sel_pass).fill(password)
    page.locator(sel_submit).click()
    try:
        page.wait_for_load_state("networkidle", timeout=60_000)
    except Exception:
        pass

    if sel_home_ready:
        page.locator(sel_home_ready).wait_for(state="visible", timeout=60_000)
    else:
        page.wait_for_timeout(2500)


def values_open_inspection_from_menu(page) -> None:
    """Best-effort: abre 'Equipo' -> 'Inspección de equipos' desde el menú.

    Esto replica el flujo que en algunos tenants dispara la carga del árbol
    completo, incluso si ya estás en la URL de inspección.
    """

    try:
        menu = page.locator("xpath=//span[normalize-space(.)='Equipo']").first
        if menu.count() == 0:
            return
        try:
            menu.click(timeout=5_000)
        except Exception:
            menu.click(timeout=5_000, force=True)
        page.wait_for_timeout(500)

        insp = page.locator(
            "xpath=//span[contains(normalize-space(.), 'Inspección de equipos')]"
        ).first
        if insp.count() == 0:
            return
        try:
            insp.click(timeout=8_000)
        except Exception:
            insp.click(timeout=8_000, force=True)
        page.wait_for_timeout(800)
        try:
            page.locator("div.el-tree").first.wait_for(state="attached", timeout=20_000)
        except Exception:
            pass
    except Exception:
        return


def safe_identifier(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "x"


def stable_suffix(value: str, *, length: int = 8) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[: max(4, length)]


@dataclass(frozen=True)
class MonitorRef:
    name: str
    url: str | None = None
    external_id: str | None = None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def db_path(base_dir: Path) -> Path:
    return base_dir / "Voltage  Values.sqlite"


def connect_db(base_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path(base_dir)))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_meta_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_monitors (
            monitor_key TEXT PRIMARY KEY,
            monitor_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_columns (
            table_name TEXT NOT NULL,
            header_text TEXT NOT NULL,
            column_name TEXT NOT NULL,
            PRIMARY KEY (table_name, header_text)
        )
        """
    )


def _existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # pragma: table_info columns => (cid, name, type, notnull, dflt_value, pk)
    return {r[1] for r in rows}


def ensure_monitor_table(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    column_names: list[str],
) -> None:
    # Tabla base mínima.
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            row_timestamp TEXT
        )
        """
    )

    existing = _existing_columns(conn, table_name)
    for col in column_names:
        if col in existing:
            continue
        # Añadimos como TEXT (no asumimos tipo).
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} TEXT")


def monitor_table_name(monitor_name: str) -> str:
    # Tabla por monitor, estable y SQL-safe.
    base = safe_identifier(monitor_name)
    # SQLite permite nombres largos pero evitamos colisiones obvias.
    return f"m_{base}_{stable_suffix(monitor_name)}"

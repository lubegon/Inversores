from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from providers.supabase_client import save_device, save_plant, save_telemetry_reading, save_plant_event

_SYNC_QUEUE: list[dict[str, Any]] = []

SHINE_URL = "https://shinemonitor.com/index_en.html"
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class PlantRef:
    plant_id: str
    name: str | None = None


def _env_flag(name: str, default: bool = False) -> bool:
    val = (os.getenv(name) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    val = (os.getenv(name) or "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _db_path(base_dir: Path) -> Path:
    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "shinemonitor.sqlite"


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"[\s-]+", "_", cleaned)
    return cleaned or "unnamed"


def _claim_friendly_table_name(
    conn: sqlite3.Connection,
    *,
    desired: str,
    device_key: str,
    plant_id: str,
) -> str:
    """Evita colisiones de nombres amigables de tablas entre distintos device_key."""
    existing = _get_device_table(conn, device_key)
    if existing:
        return existing

    base = _sanitize_name(desired)
    candidate = base
    counter = 2
    while True:
        row = conn.execute(
            "SELECT device_key FROM meta_devices WHERE table_name = ?",
            (candidate,),
        ).fetchone()

        if not row:
            return candidate

        if row[0] == device_key:
            return candidate

        candidate = f"{base}_{counter}"
        counter += 1


def _desired_table_name(
    plant_name: str | None,
    device_name: str,
    device_count_in_plant: int,
) -> str:
    if plant_name:
        p_clean = _sanitize_name(plant_name)
        if device_count_in_plant <= 1:
            return p_clean
        d_clean = _sanitize_name(device_name)
        return f"{p_clean}_{d_clean}"
    return _sanitize_name(device_name)


def _ensure_meta_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_plants (
            plant_id TEXT PRIMARY KEY,
            plant_name TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_devices (
            device_key TEXT PRIMARY KEY,
            plant_id TEXT NOT NULL,
            device_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plant_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            plant_id TEXT NOT NULL,
            plant_name TEXT,
            status TEXT NOT NULL,
            status_detail TEXT
        )
        """
    )


def _ensure_device_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            plant_id TEXT NOT NULL,
            plant_name TEXT,
            device_key TEXT NOT NULL,
            device_name TEXT NOT NULL,
            update_time TEXT,
            r_voltage REAL,
            s_voltage REAL,
            t_voltage REAL,
            rs_voltage REAL,
            st_voltage REAL,
            tr_voltage REAL,
            raw_data TEXT NOT NULL
        )
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_device_table(conn: sqlite3.Connection, device_key: str) -> str | None:
    row = conn.execute(
        "SELECT table_name FROM meta_devices WHERE device_key = ?",
        (device_key,),
    ).fetchone()
    return row[0] if row else None


def _upsert_meta_plant(
    conn: sqlite3.Connection,
    plant_id: str,
    plant_name: str | None,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO meta_plants (plant_id, plant_name, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(plant_id) DO UPDATE SET
            plant_name = excluded.plant_name,
            updated_at = excluded.updated_at
        """,
        (plant_id, plant_name, now),
    )
    _SYNC_QUEUE.append({
        "type": "plant",
        "provider": "shinemonitor",
        "plant_id": plant_id,
        "name": plant_name or str(plant_id),
        "metadata": {"updated_at": now},
    })


def _upsert_meta_device(
    conn: sqlite3.Connection,
    *,
    device_key: str,
    plant_id: str,
    device_name: str,
    table_name: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO meta_devices (device_key, plant_id, device_name, table_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_key) DO UPDATE SET
            plant_id = excluded.plant_id,
            device_name = excluded.device_name,
            table_name = excluded.table_name,
            updated_at = excluded.updated_at
        """,
        (device_key, plant_id, device_name, table_name, now),
    )
    _SYNC_QUEUE.append({
        "type": "device",
        "provider": "shinemonitor",
        "plant_id": plant_id,
        "device_key": device_key,
        "device_name": device_name,
        "metadata": {"table_name": table_name, "updated_at": now},
    })


def _insert_plant_event(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
    plant_id: str,
    plant_name: str | None,
    status: str,
    status_detail: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO plant_events (captured_at, plant_id, plant_name, status, status_detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (captured_at, plant_id, plant_name, status, status_detail),
    )
    _SYNC_QUEUE.append({
        "type": "event",
        "provider": "shinemonitor",
        "plant_id": plant_id,
        "event_type": status,
        "message": f"{plant_name or plant_id}: {status_detail or status}",
        "inserted_at": captured_at,
    })


def _launch_browser(p: Any, *, headless: bool) -> Any:
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-zygote",
    ]

    executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable_path and Path(executable_path).exists():
        return p.chromium.launch(
            headless=headless,
            executable_path=executable_path,
            args=launch_args,
        )

    project_root = Path(__file__).resolve().parents[2]
    local_browsers = project_root / "playwright_browsers"
    if local_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)

    return p.chromium.launch(headless=headless, args=launch_args)


def _dump_debug(page: Page, run_dir: Path, prefix: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(run_dir / f"{prefix}.png"), full_page=True)
        (run_dir / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def _login_if_needed(page: Page, user: str, password: str, storage_state_path: Path | None = None) -> None:
    try:
        if page.locator("#plant_tree").count() > 0 or "main.html" in (page.url or ""):
            return
    except Exception:
        pass

    usr_loc = page.locator("#loginusr > input, input[name='username'], #username").first
    pwd_loc = page.locator("#mypassword, #loginpwd > input, input[type='password']").first
    btn_loc = page.locator("#loginsub, #loginbtn, button[type='submit']").first

    try:
        usr_loc.wait_for(state="visible", timeout=3_000)
    except Exception:
        if page.locator("#plant_tree").count() > 0 or "main.html" in (page.url or ""):
            return
        return

    try:
        if pwd_loc.is_visible():
            usr_loc.fill(user)
            pwd_loc.fill(password)
            if btn_loc.is_visible():
                btn_loc.click()

            try:
                page.wait_for_url("**/main.html*", timeout=30_000)
            except Exception:
                pass

            page.wait_for_selector("#plant_tree", state="attached", timeout=60_000)
            page.wait_for_timeout(500)

            if storage_state_path:
                try:
                    page.context.storage_state(path=str(storage_state_path))
                except Exception:
                    pass
    except Exception as e:
        if page.locator("#plant_tree").count() > 0 or "main.html" in (page.url or ""):
            return
        raise e


def _tree_is_empty(tree: Locator) -> bool:
    try:
        text = (tree.inner_text() or "").strip()
        return not text
    except Exception:
        return True


def _ensure_tree_loaded(
    page: Page,
    *,
    timeout_ms: int = 20_000,
    retries: int = 2,
    run_dir: Path | None = None,
    debug_name: str = "tree-load-error",
) -> Locator:

    tree_selectors = "#plant_tree, #plantMgrTree"
    tree = page.locator(tree_selectors).first

    for attempt in range(retries + 1):
        try:
            tree.wait_for(state="attached", timeout=timeout_ms)
            page.wait_for_selector(
                "#plant_tree li.jstree-node, #plantMgrTree li.jstree-node",
                state="attached",
                timeout=timeout_ms,
            )
            if tree.locator("li.jstree-node").count() > 0:
                return tree
        except Exception:
            pass

        if attempt < retries:
            dev_tab_selectors = [
                "#plantTab a:has-text('Device Management')",
                "#plantTab a:has-text('Gestión de dispositivos')",
                "#plantTab > li:nth-child(4) > a",
                ".k-tabstrip-items li:has-text('Device Management')",
            ]
            for sel in dev_tab_selectors:
                try:
                    tab = page.locator(sel).first
                    tab.click(timeout=15000)
                    page.wait_for_timeout(1500)
                    break
                except Exception:
                    pass

    if tree.locator("li.jstree-node").count() == 0:
        if run_dir:
            _dump_debug(page, run_dir, debug_name)
        raise RuntimeError("TREE_NOT_LOADED: El árbol #plant_tree no cargó nodos jstree")

    return tree


def _select_plant_and_load_tree(
    page: Page,
    *,
    plant: PlantRef,
    run_dir: Path,
    user: str = "",
    password: str = "",
    storage_state_path: Path | None = None,
    timeout_ms: int = 60_000,
    retries: int = 1,
) -> tuple[str | None, Locator]:

    dropdown = page.locator("#headPlos > div.logo-container > div > a, #headPlos a, #headPlos, span.k-dropdown-wrap").first
    try:
        dropdown.wait_for(state="attached", timeout=4_000)
    except Exception:
        page.goto(SHINE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(500)
        if user and password:
            _login_if_needed(page, user=user, password=password, storage_state_path=storage_state_path)
        try:
            dropdown.wait_for(state="attached", timeout=timeout_ms)
        except Exception:
            pass

    try:
        dropdown.hover(timeout=2_000)
    except Exception:
        pass

    try:
        dropdown.click(timeout=5_000)
    except Exception:
        try:
            dropdown.click(timeout=5_000, force=True)
        except Exception:
            pass

    try:
        page.wait_for_selector(
            "#plantlist, #plant_select_listbox, .k-animation-container, ul.k-list",
            state="visible",
            timeout=5_000,
        )
    except Exception:
        try:
            dropdown.click(timeout=3_000, force=True)
        except Exception:
            pass
        page.wait_for_timeout(500)

    item_selectors = [
        f"#plantlist li#plant_{plant.plant_id}",
        f"#plantlist li#plant_{plant.plant_id} a",
        f"li#plant_{plant.plant_id}",
        f"#plant_select_listbox li[data-val='{plant.plant_id}']",
        f"li[data-val='{plant.plant_id}']",
    ]
    item = page.locator(", ".join(item_selectors)).first

    if item.count() == 0:
        page.wait_for_timeout(300)

    if item.count() == 0:
        _dump_debug(page, run_dir, f"{plant.plant_id}-dropdown-item-not-found")
        raise RuntimeError(
            f"No se encontró la plant_id {plant.plant_id} en el combo de plantas (#plantlist / #plant_select_listbox)"
        )

    plant_name = None
    try:
        plant_name = (item.inner_text() or "").strip() or None
    except Exception:
        pass

    # Destruir limpia e higiénicamente la instancia jstree de la planta anterior
    try:
        page.evaluate("() => { if (window.$ && $.jstree) { try { $.jstree.reference('#plant_tree').destroy(); } catch (_) {} try { $.jstree.reference('#plantMgrTree').destroy(); } catch (_) {} } }")
    except Exception:
        pass

    try:
        item.scroll_into_view_if_needed()
    except Exception:
        pass

    is_main = "main.html" in (page.url or "")

    try:
        page.evaluate(f"try {{ if (typeof currIndex !== 'undefined') currIndex = 3; }} catch (_) {{}}; getPlantId('plant_{plant.plant_id}');")
    except Exception:
        try:
            item.evaluate("el => { if (typeof currIndex !== 'undefined') currIndex = 3; if (el.tagName === 'LI') el.click(); else (el.closest('li') || el).click(); }")
        except Exception:
            item.click(force=True)

    if not is_main:
        try:
            import re
            page.wait_for_url(re.compile(r".*(main\.html|index\.html).*"), timeout=15_000)
        except Exception:
            pass

    page.wait_for_timeout(1000)

    # Asegurar que la pestaña Device Management esté visible y seleccionada si existe
    dev_tab_selectors = [
        "#plantTab a:has-text('Device Management')",
        "#plantTab a:has-text('Gestión de dispositivos')",
        "#plantTab > li:nth-child(4) > a",
        ".k-tabstrip-items li:has-text('Device Management')",
    ]
    for sel in dev_tab_selectors:
        try:
            tab = page.locator(sel).first
            tab.click(timeout=15000)
            page.wait_for_timeout(1500)
            break
        except Exception:
            pass

    tree = _ensure_tree_loaded(
        page,
        timeout_ms=timeout_ms,
        retries=retries,
        run_dir=run_dir,
        debug_name=f"{plant.plant_id}-tree-load-error",
    )

    return plant_name, tree


def _collect_inverters_and_device_anchors(tree: Locator, plant_id: str = "", timeout_ms: int = 15_000) -> tuple[int, list[Locator]]:

    page = tree.page
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        nodes = tree.locator("li.jstree-node")
        c = nodes.count()
        if c > 0:
            if not plant_id or tree.locator(f"li[id*='{plant_id}']").count() > 0 or c > 1:
                break
        page.wait_for_timeout(200)

    try:
        tree.evaluate("""(el) => {
            try {
                if (window.$ && $.jstree) {
                    $(el).jstree('open_all');
                }
            } catch (_) {}
            el.querySelectorAll('li.jstree-closed > i.jstree-ocl').forEach(icon => icon.click());
        }""")
        page.wait_for_timeout(400)
    except Exception:
        pass

    nodes = tree.locator("li.jstree-node")
    count = nodes.count()
    if count == 0:
        return 0, []

    inverter_lis: list[Locator] = []

    for i in range(count):
        node = nodes.nth(i)
        a = node.locator("> a.jstree-anchor").first
        text = (a.inner_text() or "").strip()
        node_id = (node.get_attribute("id") or "").strip()

        if text.lower().startswith("inverter") or text.lower().startswith("inversor") or node_id.startswith("inv_") or node_id.startswith("pn_"):
            inverter_lis.append(node)

    inverter_count = len(inverter_lis)
    if inverter_count == 0:
        # Fallback: si hay nodos de hoja jstree-leaf, usarlos directamente como dispositivos
        leaf_nodes = tree.locator("li.jstree-leaf > a.jstree-anchor")
        leaf_count = leaf_nodes.count()
        if leaf_count > 0:
            anchors: list[Locator] = [leaf_nodes.nth(k) for k in range(leaf_count)]
            return leaf_count, anchors
        return 0, []

    device_anchors: list[Locator] = []

    for inv_li in inverter_lis:
        icon = inv_li.locator("> i.jstree-icon.jstree-ocl").first
        try:
            is_open = inv_li.evaluate(
                "el => el.classList.contains('jstree-open')"
            )
        except Exception:
            is_open = False

        if not is_open:
            try:
                icon.click()
                inv_li.page.wait_for_timeout(200)
            except Exception:
                pass

        children = inv_li.locator("ul.jstree-children > li.jstree-node > a.jstree-anchor")
        c_count = children.count()

        for c_idx in range(c_count):
            device_anchors.append(children.nth(c_idx))

    if not device_anchors:
        # Si las sub-hojas fallaron pero encontramos nodos inverter, usar sus anchors directos
        for inv_li in inverter_lis:
            a = inv_li.locator("> a.jstree-anchor").first
            if a.count() > 0:
                device_anchors.append(a)

    return inverter_count, device_anchors


def _device_key(anchor: Locator) -> str:

    try:
        node_id = anchor.evaluate(
            "el => el.closest('li.jstree-node')?.id || ''"
        )
    except Exception:
        node_id = ""

    text = " ".join((anchor.inner_text() or "").split()).strip()

    if node_id and text:
        return f"{node_id}__{text}"
    if node_id:
        return node_id
    return text or "unknown_device"


def _click_data_details(
    page: Page,
    *,
    timeout_ms: int = 15_000,
    run_dir: Path | None = None,
    debug_name: str = "notab",
) -> bool:

    tab_selectors = [
        "a.k-link:has-text('Data Details')",
        ".k-tabstrip-items li:has-text('Data Details')",
        "li.k-item:has-text('Data Details')",
        ":text('Data Details')",
        ":text('Detalles')",
    ]
    for sel in tab_selectors:
        try:
            tab = page.locator(sel).first
            if tab.is_visible():
                tab.click()
                return True
        except Exception:
            pass

    tab = page.locator("a.k-link:has-text('Data Details'), .k-tabstrip-items li:has-text('Data Details'), :text('Data Details')").first
    try:
        tab.wait_for(state="visible", timeout=timeout_ms)
        tab.click()
        return True
    except Exception:
        if run_dir:
            _dump_debug(page, run_dir, debug_name)
        return False


def _is_grid_stale(grid_el: Locator, last_signature: str | None) -> bool:

    if not last_signature:
        return False
    try:
        curr = (grid_el.inner_text() or "").strip()
        return curr == last_signature
    except Exception:
        return False


def _get_grid_signature(grid_el: Locator) -> str | None:

    try:
        t = (grid_el.inner_text() or "").strip()
        return t if t else None
    except Exception:
        return None


def _find_kendo_grid(page: Page) -> Locator | None:

    candidates = [
        "div.k-grid:has(table.k-printable)",
        "div.k-grid:has(th:has-text('Update Time'))",
        "div.k-grid:has(th:has-text('Voltage'))",
        "div.k-grid",
        "table#invDetailTable",
        "table.tableStyle:has(th:has-text('Timestamp'))",
        "table.tableStyle:has(th:has-text('Voltage'))",
        "table.tableStyle:has(th:has-text('Voltaje'))",
        "table.tableStyle",
    ]

    for sel in candidates:
        loc = page.locator(sel).first
        if loc.is_visible():
            return loc
    return None


def _click_grid_refresh_button(page: Page) -> bool:

    btn_selectors = [
        "a.k-pager-refresh",
        "a.k-button-icontext:has-text('Refresh')",
        "button:has-text('Refresh')",
        ".k-pager-wrap a[title*='Refresh']",
    ]

    for sel in btn_selectors:
        loc = page.locator(sel).first
        if loc.is_visible():
            loc.click()
            return True
    return False


def _ensure_grid_data(
    page: Page,
    *,
    timeout_ms: int = 30_000,
    attempts: int = MAX_ATTEMPTS,
    last_signature: str | None = None,
) -> tuple[Locator, list[str]]:

    grid = _find_kendo_grid(page)
    if not grid:
        try:
            page.locator("div.k-grid:visible, table.tableStyle:visible, table#invDetailTable:visible, #invDetailCue:visible, div.faultInfo:visible").first.wait_for(timeout=timeout_ms)
        except Exception:
            pass
        grid = _find_kendo_grid(page)

    if not grid:
        err_msg = page.locator("#invDetailCue, div.faultInfo").first
        if err_msg.is_visible():
            text = (err_msg.inner_text() or "").lower()
            if "no detail data" in text or "no data" in text:
                raise RuntimeError("NO_DATA_TODAY: " + text)
        raise RuntimeError("No se encontró ningún div.k-grid o table visible")

    rows_loc = grid.locator("tbody > tr")

    import time
    for attempt in range(1, attempts + 1):
        try:
            end_time = time.time() + (timeout_ms / 1000.0)
            while time.time() < end_time:
                if rows_loc.count() > 0 and rows_loc.first.is_visible():
                    break
                
                err_msg = page.locator("#invDetailCue, div.faultInfo").first
                if err_msg.is_visible():
                    text = (err_msg.inner_text() or "").lower()
                    if "no detail data" in text or "no data" in text:
                        raise RuntimeError("NO_DATA_TODAY: " + text)
                        
                page.wait_for_timeout(500)
            else:
                rows_loc.first.wait_for(state="visible", timeout=1000)

            stale = _is_grid_stale(grid, last_signature)

            rows_count = rows_loc.count()
            if rows_count > 0 and not stale:

                headers = [
                    (th.inner_text() or "").strip()
                    for th in grid.locator("th").all()
                ]

                if any(h for h in headers):
                    return grid, headers

        except Exception as e:
            if "NO_DATA_TODAY" in str(e):
                raise
            pass

        refreshed = _click_grid_refresh_button(page)

        page.wait_for_timeout(200)

    headers = [
        (th.inner_text() or "").strip() for th in grid.locator("th").all()
    ]
    return grid, headers


def _parse_float(val: str | None) -> float | None:
    if not val:
        return None
    cleaned = val.strip().replace(",", ".")
    m = re.search(r"[-+]?\d*\.?\d+", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _extract_grid_data(
    grid: Locator, headers: list[str]
) -> tuple[str | None, dict[str, float | None], dict[str, Any]]:

    rows = grid.locator("tbody > tr")

    num_rows = rows.count()

    if num_rows == 0:
        return None, {}, {"headers": headers, "rows": []}

    first_row = rows.first
    cells = [
        (td.inner_text() or "").strip() for td in first_row.locator("td").all()
    ]

    raw_data: dict[str, Any] = {
        "headers": headers,
        "first_row_cells": cells,
        "total_rows_in_grid": num_rows,
    }

    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        h_norm = h.lower().replace("\n", " ").strip()
        col_map[h_norm] = idx

    def get_cell_by_headers(possible_headers: list[str]) -> str | None:
        for ph in possible_headers:
            ph_norm = ph.lower()
            for h_norm, idx in col_map.items():
                if ph_norm in h_norm:
                    if idx < len(cells):
                        return cells[idx]
        return None

    update_time = get_cell_by_headers(
        ["timestamp", "update time", "data time", "time", "fecha", "hora"]
    )

    if not update_time and len(cells) > 0:

        for c in cells:
            if re.search(r"\d{4}-\d{2}-\d{2}", c) or re.search(
                r"\d{2}:\d{2}", c
            ):
                update_time = c
                break

    voltages: dict[str, float | None] = {
        "r_voltage": None,
        "s_voltage": None,
        "t_voltage": None,
        "rs_voltage": None,
        "st_voltage": None,
        "tr_voltage": None,
    }

    mapping = [
        ("r_voltage", ["grid voltage", "r voltage", "phase a voltage", "va", "voltage r", "grid voltage r"]),
        ("s_voltage", ["inverter voltage", "s voltage", "phase b voltage", "vb", "voltage s", "grid voltage s"]),
        ("t_voltage", ["pv voltage", "t voltage", "phase c voltage", "vc", "voltage t", "grid voltage t"]),
        ("rs_voltage", ["rs voltage", "vrs", "line voltage rs", "uab"]),
        ("st_voltage", ["st voltage", "vst", "line voltage st", "ubc"]),
        ("tr_voltage", ["tr voltage", "vtr", "line voltage tr", "uca"]),
    ]

    for key, possible_names in mapping:
        val_str = get_cell_by_headers(possible_names)
        voltages[key] = _parse_float(val_str)

    if all(v is None for v in voltages.values()):

        voltage_cells: list[tuple[str, float]] = []

        for idx, (h, c) in enumerate(zip(headers, cells)):
            if "volt" in h.lower() or "v" in h.lower():
                parsed = _parse_float(c)
                if parsed is not None:
                    voltage_cells.append((h, parsed))

        if len(voltage_cells) >= 1:
            voltages["r_voltage"] = voltage_cells[0][1]
        if len(voltage_cells) >= 2:
            voltages["s_voltage"] = voltage_cells[1][1]
        if len(voltage_cells) >= 3:
            voltages["t_voltage"] = voltage_cells[2][1]

    parsed_cells: dict[str, Any] = {}
    for h, c in zip(headers, cells):
        if h and c:
            clean_h = h.strip()
            parsed_cells[clean_h] = c.strip()
            parsed_val = _parse_float(c)
            if parsed_val is not None:
                parsed_cells[f"{clean_h}_num"] = parsed_val

    raw_data["parsed_cells"] = parsed_cells

    return update_time, voltages, raw_data


def _insert_voltage_reading(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    captured_at: str,
    plant_id: str,
    plant_name: str | None,
    device_key: str,
    device_name: str,
    update_time: str | None,
    voltages: dict[str, float | None],
    raw_data: dict[str, Any],
) -> None:

    conn.execute(
        f"""
        INSERT INTO "{table_name}" (
            captured_at, plant_id, plant_name, device_key, device_name,
            update_time, r_voltage, s_voltage, t_voltage,
            rs_voltage, st_voltage, tr_voltage, raw_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            captured_at,
            plant_id,
            plant_name,
            device_key,
            device_name,
            update_time,
            voltages.get("r_voltage"),
            voltages.get("s_voltage"),
            voltages.get("t_voltage"),
            voltages.get("rs_voltage"),
            voltages.get("st_voltage"),
            voltages.get("tr_voltage"),
            json.dumps(raw_data, ensure_ascii=False),
        ),
    )

    metrics = {
        "r_voltage": voltages.get("r_voltage"),
        "s_voltage": voltages.get("s_voltage"),
        "t_voltage": voltages.get("t_voltage"),
        "rs_voltage": voltages.get("rs_voltage"),
        "st_voltage": voltages.get("st_voltage"),
        "tr_voltage": voltages.get("tr_voltage"),
        "table_name": table_name,
        "plant_id": plant_id,
        "plant_name": plant_name or "",
        "device_name": device_name or "",
    }
    parsed_cells = raw_data.get("parsed_cells")
    if isinstance(parsed_cells, dict):
        metrics.update(parsed_cells)

    status = "OK" if (update_time or any(v is not None for v in voltages.values())) else "NO_DATA"

    _SYNC_QUEUE.append({
        "type": "telemetry",
        "device_key": device_key,
        "plant_id": plant_id,
        "provider": "shinemonitor",
        "update_time": update_time or "",
        "status": status,
        "metrics": metrics,
        "raw_data": raw_data,
        "inserted_at": captured_at,
    })


def _load_plants(storage_dir: Path) -> list[PlantRef]:
    snapshot_path = storage_dir / "shinemonitor-plants.json"
    if not snapshot_path.exists():
        raise SystemExit(
            "No existe storage/shinemonitor-plants.json. Ejecuta primero shinemonitor_discover_plants.py"
        )

    data: dict[str, Any] = json.loads(snapshot_path.read_text(encoding="utf-8"))
    plants_raw: Iterable[dict[str, Any]] = data.get("plants") or []
    plants: list[PlantRef] = []
    for p in plants_raw:
        plant_id = str(p.get("plant_id") or "").strip()
        if not plant_id:
            continue
        name = p.get("name")
        plants.append(PlantRef(plant_id=plant_id, name=name))
    return plants


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

    default_timeout_ms = _env_int("SHINE_DEFAULT_TIMEOUT_MS", 15_000)
    nav_timeout_ms = _env_int("SHINE_NAV_TIMEOUT_MS", 30_000)

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage_state_path = storage_dir / "shinemonitor.json"

    only_plant_id = (os.getenv("PLANT_ID") or "").strip()

    plants = _load_plants(storage_dir)
    if only_plant_id:
        plants = [p for p in plants if p.plant_id == only_plant_id]

    if not plants:
        raise SystemExit("No hay plants para procesar (revisa PLANT_ID o el snapshot)")

    captured_at = datetime.now(timezone.utc).isoformat()

    db_path = _db_path(base_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        _ensure_meta_tables(conn)
        conn.commit()

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=headless)
            if storage_state_path.exists():
                context = browser.new_context(storage_state=str(storage_state_path))
            else:
                context = browser.new_context()

            page = context.new_page()
            page.set_default_timeout(default_timeout_ms)
            page.set_default_navigation_timeout(nav_timeout_ms)

            run_dir = storage_dir / "scrape"
            run_dir.mkdir(parents=True, exist_ok=True)

            try:
                page.goto(SHINE_URL, wait_until="domcontentloaded", timeout=nav_timeout_ms)
                page.wait_for_timeout(300)
                _login_if_needed(page, user=user, password=password, storage_state_path=storage_state_path)

                for idx, plant in enumerate(plants, start=1):
                    print(f"[{idx}/{len(plants)}] Plant {plant.plant_id}...", flush=True)

                    plant_name: str | None = None
                    try:
                        _login_if_needed(page, user=user, password=password, storage_state_path=storage_state_path)

                        page.wait_for_timeout(200)
                        plant_name, tree = _select_plant_and_load_tree(
                            page,
                            plant=plant,
                            run_dir=run_dir,
                            user=user,
                            password=password,
                            storage_state_path=storage_state_path,
                            timeout_ms=20_000,
                            retries=1,
                        )

                        _upsert_meta_plant(conn, plant.plant_id, plant_name, captured_at)
                        conn.commit()
                    except Exception as e:
                        detail = str(e)
                        _insert_plant_event(
                            conn,
                            captured_at=captured_at,
                            plant_id=plant.plant_id,
                            plant_name=plant_name or plant.name,
                            status="ERROR",
                            status_detail=detail[:500] if detail else None,
                        )
                        conn.commit()
                        _dump_debug(page, run_dir, f"{plant.plant_id}-plant-error")
                        continue

                    inverter_count, device_anchors = _collect_inverters_and_device_anchors(tree, plant_id=plant.plant_id)
                    if inverter_count == 0:
                        if tree.locator("li.jstree-node").count() == 0:
                            print("  - TREE_LOAD_ERROR", flush=True)
                            _insert_plant_event(
                                conn,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                status="TREE_LOAD_ERROR",
                                status_detail="No se cargaron nodos jstree en el árbol de la planta",
                            )
                            conn.commit()
                            _dump_debug(page, run_dir, f"{plant.plant_id}-tree-empty")
                            continue

                        print("  - NO_INVERTER", flush=True)
                        _insert_plant_event(
                            conn,
                            captured_at=captured_at,
                            plant_id=plant.plant_id,
                            plant_name=plant_name,
                            status="NO_INVERTER",
                            status_detail="No hay Inverter en este Monitor",
                        )
                        conn.commit()
                        page.screenshot(
                            path=str(run_dir / f"{plant.plant_id}-02-no-inverter.png"),
                            full_page=True,
                        )
                        continue

                    if not device_anchors:
                        _insert_plant_event(
                            conn,
                            captured_at=captured_at,
                            plant_id=plant.plant_id,
                            plant_name=plant_name,
                            status="NO_DEVICES",
                            status_detail="El nodo Inverter no tiene monitores hijos",
                        )
                        conn.commit()
                        page.screenshot(
                            path=str(run_dir / f"{plant.plant_id}-02-inverter-empty.png"),
                            full_page=True,
                        )
                        continue

                    device_count = len(device_anchors)

                    for dev_index in range(device_count):
                        try:
                            page.wait_for_timeout(200)
                            tree = _ensure_tree_loaded(
                                page,
                                timeout_ms=20_000,
                                retries=1,
                                run_dir=run_dir,
                                debug_name=f"{plant.plant_id}-tree-reload-{dev_index+1:02d}",
                            )
                            if _tree_is_empty(tree):
                                raise RuntimeError("TREE_EMPTY")
                        except Exception as e:
                            retryable = isinstance(e, PlaywrightTimeoutError) or (str(e).strip() == "TREE_EMPTY")
                            if retryable:
                                try:
                                    print(
                                        f"  [RETRY] re-cargando árbol (device {dev_index+1}/{device_count})",
                                        flush=True,
                                    )
                                except Exception:
                                    pass
                                try:
                                    plant_name, tree = _select_plant_and_load_tree(
                                        page,
                                        plant=plant,
                                        run_dir=run_dir,
                                        user=user,
                                        password=password,
                                        storage_state_path=storage_state_path,
                                        timeout_ms=20_000,
                                        retries=0,
                                    )
                                except Exception as e2:
                                    detail = str(e2) or str(e)
                                    _insert_plant_event(
                                        conn,
                                        captured_at=captured_at,
                                        plant_id=plant.plant_id,
                                        plant_name=plant_name,
                                        status="TREE_RELOAD_ERROR",
                                        status_detail=(detail[:500] if detail else None),
                                    )
                                    conn.commit()
                                    _dump_debug(
                                        page,
                                        run_dir,
                                        f"{plant.plant_id}-03-tree-reload-error-{dev_index+1:02d}",
                                    )
                                    break
                            else:
                                detail = str(e)
                                _insert_plant_event(
                                    conn,
                                    captured_at=captured_at,
                                    plant_id=plant.plant_id,
                                    plant_name=plant_name,
                                    status="TREE_RELOAD_ERROR",
                                    status_detail=(detail[:500] if detail else None),
                                )
                                conn.commit()
                                _dump_debug(
                                    page,
                                    run_dir,
                                    f"{plant.plant_id}-03-tree-reload-error-{dev_index+1:02d}",
                                )
                                break

                        try:
                            _, anchors = _collect_inverters_and_device_anchors(tree)
                        except Exception as e:
                            detail = str(e)
                            _insert_plant_event(
                                conn,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                status="TREE_PARSE_ERROR",
                                status_detail=(detail[:500] if detail else None),
                            )
                            conn.commit()
                            _dump_debug(page, run_dir, f"{plant.plant_id}-03-tree-parse-error-{dev_index+1:02d}")
                            break

                        if dev_index >= len(anchors):
                            break

                        a = anchors[dev_index]
                        device_name = " ".join(a.inner_text().split()).strip() or f"device_{dev_index+1}"
                        device_key = _device_key(a)

                        desired = _desired_table_name(plant_name, device_name, device_count)
                        desired = _claim_friendly_table_name(
                            conn,
                            desired=desired,
                            device_key=device_key,
                            plant_id=plant.plant_id,
                        )

                        existing = _get_device_table(conn, device_key)
                        if existing and existing != desired and _table_exists(conn, existing):
                            conn.execute(f'ALTER TABLE "{existing}" RENAME TO "{desired}"')
                            conn.commit()

                        device_table = desired

                        _ensure_device_table(conn, device_table)
                        _upsert_meta_device(
                            conn,
                            device_key=device_key,
                            plant_id=plant.plant_id,
                            device_name=device_name,
                            table_name=device_table,
                            now=captured_at,
                        )

                        print(f"  - Device [{dev_index+1}/{len(device_anchors)}]: {device_name}", flush=True)

                        a.click()
                        page.wait_for_timeout(300)
                        opened = _click_data_details(
                            page,
                            timeout_ms=10_000,
                            run_dir=run_dir,
                            debug_name=f"{plant.plant_id}-03-notab-{dev_index+1:02d}",
                        )
                        if not opened:
                            try:
                                print(
                                    f"  [RETRY] Data Details no aparece; reintentando planta/device...",
                                    flush=True,
                                )
                            except Exception:
                                pass

                            try:
                                plant_name_retry, tree_retry = _select_plant_and_load_tree(
                                    page,
                                    plant=plant,
                                    run_dir=run_dir,
                                    user=user,
                                    password=password,
                                    storage_state_path=storage_state_path,
                                    timeout_ms=20_000,
                                    retries=1,
                                )
                                if plant_name_retry:
                                    plant_name = plant_name_retry
                                _, anchors_retry = _collect_inverters_and_device_anchors(tree_retry)
                                if dev_index < len(anchors_retry):
                                    a_retry = anchors_retry[dev_index]
                                    a_retry.click()
                                    page.wait_for_timeout(300)
                                    opened = _click_data_details(
                                        page,
                                        timeout_ms=10_000,
                                        run_dir=run_dir,
                                        debug_name=f"{plant.plant_id}-03-notab-retry-{dev_index+1:02d}",
                                    )
                            except Exception:
                                pass

                        if not opened:
                            _insert_plant_event(
                                conn,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                status="DATA_DETAILS_TIMEOUT",
                                status_detail=f"No apareció pestaña Data Details para {device_name}",
                            )
                            conn.commit()
                            continue

                        last_sig: str | None = None
                        try:
                            grid_el, headers = _ensure_grid_data(
                                page,
                                timeout_ms=45_000,
                                attempts=MAX_ATTEMPTS,
                                last_signature=None,
                            )
                            update_time, voltages, raw_data = _extract_grid_data(grid_el, headers)
                            last_sig = _get_grid_signature(grid_el)
                        except Exception as e:
                            detail = str(e)
                            if "NO_DATA_TODAY" in detail:
                                _insert_plant_event(
                                    conn,
                                    captured_at=captured_at,
                                    plant_id=plant.plant_id,
                                    plant_name=plant_name,
                                    status="NO_DATA_TODAY",
                                    status_detail=f"Sin datos hoy para ({device_name})",
                                )
                                conn.commit()
                                print(f"    -> Sin datos hoy para {device_name}", flush=True)
                                continue

                            _insert_plant_event(
                                conn,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                status="GRID_ERROR",
                                status_detail=f"Error al leer grid ({device_name}): {detail[:300]}",
                            )
                            conn.commit()
                            _dump_debug(
                                page,
                                run_dir,
                                f"{plant.plant_id}-04-grid-error-{dev_index+1:02d}",
                            )
                            continue

                        if not update_time and all(v is None for v in voltages.values()):
                            print("    * (Reintento de lectura por tabla vacía...)", flush=True)
                            try:
                                _click_grid_refresh_button(page)
                                page.wait_for_timeout(300)
                                grid_el, headers = _ensure_grid_data(
                                    page,
                                    timeout_ms=45_000,
                                    attempts=2,
                                    last_signature=last_sig,
                                )
                                update_time, voltages, raw_data = _extract_grid_data(grid_el, headers)
                            except Exception:
                                pass

                        _insert_voltage_reading(
                            conn,
                            table_name=device_table,
                            captured_at=captured_at,
                            plant_id=plant.plant_id,
                            plant_name=plant_name,
                            device_key=device_key,
                            device_name=device_name,
                            update_time=update_time,
                            voltages=voltages,
                            raw_data=raw_data,
                        )
                        conn.commit()

                        print(
                            f"    -> OK ({device_table}) update_time={update_time} voltages={voltages}",
                            flush=True,
                        )

                page.wait_for_timeout(200)

            except Exception as e:
                _dump_debug(page, run_dir, "fatal-error")
                raise e
            finally:
                context.close()
                browser.close()
    finally:
        conn.close()

    _process_sync_queue()


def _process_sync_queue() -> None:
    if not _SYNC_QUEUE:
        return
    
    print(f"\\nIniciando sincronizacion con Supabase ({len(_SYNC_QUEUE)} elementos)...", flush=True)
    for item in _SYNC_QUEUE:
        try:
            t = item["type"]
            if t == "plant":
                save_plant(
                    provider=item["provider"],
                    plant_id=item["plant_id"],
                    name=item["name"],
                    metadata=item["metadata"],
                )
            elif t == "device":
                save_device(
                    provider=item["provider"],
                    plant_id=item["plant_id"],
                    device_key=item["device_key"],
                    device_name=item["device_name"],
                    metadata=item["metadata"],
                )
            elif t == "event":
                save_plant_event(
                    provider=item["provider"],
                    plant_id=item["plant_id"],
                    event_type=item["event_type"],
                    message=item["message"],
                    inserted_at=item["inserted_at"],
                )
            elif t == "telemetry":
                save_telemetry_reading(
                    device_key=item["device_key"],
                    plant_id=item["plant_id"],
                    provider=item["provider"],
                    update_time=item["update_time"],
                    status=item["status"],
                    metrics=item["metrics"],
                    raw_data=item["raw_data"],
                    inserted_at=item["inserted_at"],
                )
        except Exception as e:
            print(f"Error subiendo a Supabase: {e}", flush=True)

    _SYNC_QUEUE.clear()


if __name__ == "__main__":
    main()

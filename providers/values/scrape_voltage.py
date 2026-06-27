"""Scraper Values: Historial -> primera fila -> SQLite.

Lee `storage/values-monitors.json` y, por cada monitor:
- lo selecciona en el árbol (Almacenamiento de energía)
- abre el detalle `#maxSeld`
- hace click en el ícono de Historial
- extrae SOLO la primera fila de la tabla de Historial
- persiste en SQLite (`Voltage  Values.sqlite`) con una tabla por monitor

Config `.env`:
- `VALUES_INSPECTION_URL`
- `HEADLESS` (default True)
- `VALUES_LIMIT_MONITORS` (opcional)
- `VALUES_TURBO` (opcional; 1 evita reset pesado por monitor)
- `VALUES_USE_DEVICE_LIST` (opcional; default: igual a TURBO; abre monitores desde la lista/búsqueda)

Selectores Historial (opcionales):
- `VALUES_SEL_HISTORY_ICON` (puede ser selector único o lista separada por `||` o JSON array)
- `VALUES_SEL_HISTORY_READY` (idem; por defecto espera una `div.el-table` bajo `#maxSeld`)

Selectores tabla (opcionales):
- `VALUES_SEL_HISTORY_TABLE` (default: "#maxSeld div.el-table")
- `VALUES_SEL_HISTORY_HEADER` (default: "thead tr th")
- `VALUES_SEL_HISTORY_ROW` (default: "tbody tr")
- `VALUES_SEL_HISTORY_CELLS` (default: "td")
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import (
    MonitorRef,
    connect_db,
    dump_debug,
    ensure_meta_tables,
    ensure_monitor_table,
    env_flag,
    launch_browser,
    values_login_if_needed,
    values_open_inspection_from_menu,
    monitor_table_name,
    read_json,
    safe_identifier,
    stable_suffix,
)

from .navigate_history import (
    _ensure_energy_storage_tree_open,
    _expand_energy_storage,
    _find_monitor_node_with_scroll,
    _get_tree_root,
    _open_detail_from_device_list,
    _reset_to_inspection_for_next,
    _return_to_device_list,
    _tree_get_scroll_el,
    _tree_scroll_state,
)

from . import navigate_history as _navhist


VALUES_COLUMNS_TO_EXTRACT: list[str] = [
    "Marca de Tiempo",
    "Battery Voltage(V)",
    "PV1 Voltage(V)",
    "PV2 Voltage(V)",
    "Inverter Voltage(V)",
    "BMS battery voltage(V)",
    "load current(A)",
    "Batt Current(A)",
    "PV1 Charger Current(A)",
    "PV1 Charger Power(W)",
    "PV2 Charger Power(W)",
    "PV Total Charger Power(W)",
    "SOC(%)",
    "PLoad(W)",
    "PGrid(W)",
    "work state",
    "Grid Voltage(V)",
    "Software version",
    "rated power(W)",
    "Inverter current(A)",
    "grid current(A)",
    "PInverter(W)",
    "SInverter(VA)",
    "SGrid(VA)",
    "SLoad(VA)",
    "Qinverter(var)",
    "Qgrid(var)",
    "Qload(var)",
    "Inverter frequency(Hz)",
    "Grid frequency(Hz)",
    "AC radiator temperature(°C)",
    "Transformer temperature(°C)",
    "DC radiator temperature(°C)",
    "accumulated charger power(kWh)",
    "accumulated discharger power(kWh)",
    "accumulated buy power(kWh)",
    "Accumulated Sell Power(kWh)",
    "Accumulated Load Power(kWh)",
    "Accumulated Self_Use Power(kWh)",
    "batt power(W)",
    "charger work enable",
    "PV Cumulative Power Generation(kWh)",
    "BMS battery temperature(°C)",
]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip().replace("_", ""))
    except Exception:
        return default


def _parse_selector_list(raw: str, defaults: list[str]) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return defaults
    if raw.startswith("["):
        try:
            import json as _json

            parsed = _json.loads(raw)
            out = [str(x).strip() for x in parsed if str(x).strip()]
            return out or defaults
        except Exception:
            return defaults
    if "||" in raw:
        out = [s.strip() for s in raw.split("||") if s.strip()]
        return out or defaults
    return [raw]


def _click_first_fast(page, selectors: list[str], *, timeout_ms: int = 6_000) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            try:
                loc.wait_for(state="attached", timeout=max(1_000, int(timeout_ms / 2)))
            except Exception:
                continue
        try:
            loc.click(timeout=8_000)
        except Exception:
            try:
                loc.click(timeout=8_000, force=True)
            except Exception:
                continue
        return True
    return False


def _wait_history_headers(page, *, timeout_ms: int = 20_000) -> None:
    """Espera a que la tabla de Historial muestre headers esperados.

    Evita extraer desde otro `el-table` dentro de `#maxSeld`.
    """

    expected = [
        "battery voltage",
        "pv1 voltage",
        "soc",
        "pload",
    ]
    page.wait_for_function(
        r"""(expected) => {
  const root = document.querySelector('#maxSeld');
  if (!root) return false;
  const tables = Array.from(root.querySelectorAll('div.el-table'));
  if (!tables.length) return false;
  const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
  for (const t of tables) {
    const ths = Array.from(t.querySelectorAll('.el-table__header-wrapper th'));
    const txt = norm(ths.map(th => (th.innerText || th.textContent || '')).join(' '));
    let hits = 0;
    for (const k of expected) {
      if (txt.includes(k)) hits++;
    }
    if (hits >= 2) return true;
  }
  return false;
}""",
        expected,
        timeout=timeout_ms,
    )


def _resolve_history_table_locator(page, *, base_sel: str):
    """Devuelve el locator de la tabla ElementUI dentro de #maxSeld que parece ser Historial."""

    candidates = page.locator(base_sel)
    try:
        n = candidates.count()
    except Exception:
        n = 0
    n = min(n, 8)
    if n <= 0:
        return page.locator(base_sel).first

    keys = [
        "battery voltage",
        "pv1 voltage",
        "soc",
        "pload",
    ]
    for i in range(n):
        t = candidates.nth(i)
        try:
            ths = t.locator(".el-table__header-wrapper th")
            header_txt = " ".join([x.strip() for x in ths.all_inner_texts() if x and x.strip()]).lower()
        except Exception:
            header_txt = ""
        hits = sum(1 for k in keys if k in header_txt)
        if hits >= 2:
            return t

    return candidates.first


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_header_key(text: str) -> str:
    # Normalización tolerante para empatar headers UI (mayúsculas, espacios, guiones, etc.).
    t = " ".join((text or "").split()).strip().lower()
    t = t.replace("_", " ")
    t = " ".join(t.split())
    return t


def _filter_row_to_fixed_columns(
    row_data: dict[str, str],
    *,
    fixed_columns: list[str],
) -> dict[str, str | None]:
    by_norm: dict[str, str] = {}
    for k, v in row_data.items():
        nk = _normalize_header_key(k)
        by_norm[nk] = v
    out: dict[str, str | None] = {}
    for col in fixed_columns:
        nk = _normalize_header_key(col)
        out[col] = by_norm.get(nk)
    return out

    fixed_row = table.locator(".el-table__fixed-body-wrapper tbody tr").first
    main_row = table.locator(".el-table__body-wrapper tbody tr").first

    out: dict[str, str | None] = {}
    for wanted in fixed_columns:
        nk = _normalize_header_key(wanted)
        col_info = header_map.get(nk)
        if not col_info:
            out[wanted] = None
            continue

        col_cls, is_fixed = col_info
        row = fixed_row if is_fixed else main_row

        val = ""
        try:
            cell = row.locator(f"td.{col_cls} .cell").first
            val = " ".join((cell.inner_text() or "").split()).strip()
        except Exception:
            try:
                cell = row.locator(f"td.{col_cls}").first
                val = " ".join((cell.inner_text() or "").split()).strip()
            except Exception:
                val = ""

        out[wanted] = val if val != "" else None

    return out


def _build_elementui_header_map(table) -> dict[str, tuple[str, bool]]:
    """Construye un mapa normalizado header->(col_class, is_fixed) para ElementUI.

    Se calcula 1 vez y se reutiliza entre monitores para acelerar.
    """

    table = table.first
    table.wait_for(state="attached", timeout=60_000)

    def _read_header_map(th_locator, *, is_fixed: bool) -> dict[str, tuple[str, bool]]:
        mapping: dict[str, tuple[str, bool]] = {}
        try:
            n = th_locator.count()
        except Exception:
            n = 0

        for i in range(n):
            th = th_locator.nth(i)
            try:
                txt = " ".join((th.inner_text() or "").split()).strip()
            except Exception:
                txt = ""
            if not txt:
                continue

            try:
                cls = th.get_attribute("class") or ""
            except Exception:
                cls = ""

            m = re.search(r"(el-table_\d+_column_\d+)", cls)
            if not m:
                continue
            col_cls = m.group(1)

            nk = _normalize_header_key(txt)
            if nk and nk not in mapping:
                mapping[nk] = (col_cls, is_fixed)
        return mapping

    header_map: dict[str, tuple[str, bool]] = {}
    fixed_ths = table.locator(".el-table__fixed-header-wrapper th")
    main_ths = table.locator(".el-table__header-wrapper th")

    header_map.update(_read_header_map(fixed_ths, is_fixed=True))
    for k, v in _read_header_map(main_ths, is_fixed=False).items():
        if k not in header_map:
            header_map[k] = v

    return header_map


def _extract_first_row_fixed_columns_elementui_cached(
    page,
    *,
    table,
    fixed_columns: list[str],
    header_map: dict[str, tuple[str, bool]],
) -> dict[str, str | None]:
    """Extrae la primera fila usando un header_map cacheado y lectura en bloque (evaluate).

    Reduce roundtrips Playwright (muy importante para performance).
    """

    table = table.first
    table.wait_for(state="visible", timeout=60_000)

    # Asegurar que haya al menos una fila renderizada.
    try:
        table.locator(".el-table__body-wrapper tbody tr").first.wait_for(state="visible", timeout=60_000)
    except Exception:
        table.locator(".el-table__fixed-body-wrapper tbody tr").first.wait_for(state="visible", timeout=60_000)

    h = table.element_handle()
    if h is None:
        raise RuntimeError("No se pudo obtener element_handle() de la tabla")

    row_maps = h.evaluate(
        r"""(root) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const readRow = (rowSel) => {
    const row = root.querySelector(rowSel);
    if (!row) return { byClass: {}, firstText: '' };
    const tds = Array.from(row.querySelectorAll('td'));
    const byClass = {};
    let firstText = '';
    for (let i = 0; i < tds.length; i++) {
      const td = tds[i];
      const m = (td.className || '').match(/(el-table_\d+_column_\d+)/);
      const cls = m ? m[1] : '';
      const cell = td.querySelector('.cell');
      const txt = norm(cell ? cell.innerText : td.innerText);
      if (i === 0) firstText = txt;
      if (cls) byClass[cls] = txt;
    }
    return { byClass, firstText };
  };
  return {
    fixed: readRow('.el-table__fixed-body-wrapper tbody tr'),
    main: readRow('.el-table__body-wrapper tbody tr'),
  };
}""",
    )

    fixed_by_cls = (row_maps or {}).get("fixed", {}).get("byClass", {}) or {}
    main_by_cls = (row_maps or {}).get("main", {}).get("byClass", {}) or {}
    fixed_first = (row_maps or {}).get("fixed", {}).get("firstText", "") or ""

    out: dict[str, str | None] = {}
    for wanted in fixed_columns:
        nk = _normalize_header_key(wanted)
        col_info = header_map.get(nk)
        if col_info:
            col_cls, is_fixed = col_info
            raw = fixed_by_cls.get(col_cls) if is_fixed else main_by_cls.get(col_cls)
            val = " ".join(str(raw or "").split()).strip()
            out[wanted] = val if val else None
        else:
            out[wanted] = None

    # Fallback: si "Marca de Tiempo" no se pudo mapear por header, intentar desde primera celda fija.
    if (
        out.get("Marca de Tiempo") is None
        or str(out.get("Marca de Tiempo") or "").strip() == ""
    ) and fixed_first:
        out["Marca de Tiempo"] = fixed_first

    return out


def _extract_first_row(page, *, table_sel: str, header_sel: str, row_sel: str, cell_sel: str):
    table = page.locator(table_sel).first
    table.wait_for(state="visible", timeout=60_000)

    # Esperar a que al menos una celda tenga texto (a veces la tabla aparece antes que los datos).
    try:
        main_first_cell = table.locator(".el-table__body-wrapper tbody tr").first.locator("td .cell").first
        fixed_first_cell = table.locator(".el-table__fixed-body-wrapper tbody tr").first.locator("td .cell").first
        for _ in range(40):
            main_txt = ""
            fixed_txt = ""
            try:
                main_txt = (main_first_cell.inner_text() or "").strip()
            except Exception:
                pass
            try:
                fixed_txt = (fixed_first_cell.inner_text() or "").strip()
            except Exception:
                pass
            if main_txt or fixed_txt:
                break
            page.wait_for_timeout(500)
    except Exception:
        pass

    def _best_text_list(selectors: list[str]) -> list[str]:
        best: list[str] = []
        best_non_empty = -1
        best_count = -1
        for sel in selectors:
            loc = table.locator(sel)
            texts: list[str] = []
            non_empty = 0
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    txt = " ".join((loc.nth(i).inner_text() or "").split()).strip()
                except Exception:
                    txt = ""
                texts.append(txt)
                if txt:
                    non_empty += 1
            if non_empty > best_non_empty or (non_empty == best_non_empty and len(texts) > best_count):
                best = texts
                best_non_empty = non_empty
                best_count = len(texts)
        return best

    def _read_part(
        *,
        headers_selectors: list[str],
        row_selector: str,
        cell_selectors: list[str],
    ) -> tuple[list[str], list[str]]:
        headers_raw = _best_text_list(headers_selectors)
        headers = [h for h in headers_raw if h]

        row = table.locator(row_selector).first
        row.wait_for(state="visible", timeout=60_000)
        best_values: list[str] = []
        best_non_empty = -1
        best_count = -1
        for sel in cell_selectors:
            loc = row.locator(sel)
            values: list[str] = []
            non_empty = 0
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    val = " ".join((loc.nth(i).inner_text() or "").split()).strip()
                except Exception:
                    val = ""
                values.append(val)
                if val:
                    non_empty += 1
            if non_empty > best_non_empty or (non_empty == best_non_empty and len(values) > best_count):
                best_values = values
                best_non_empty = non_empty
                best_count = len(values)

        return headers, best_values

    def _pair(headers: list[str], values: list[str]) -> dict[str, str]:
        data: dict[str, str] = {}
        for idx, val in enumerate(values):
            key = headers[idx] if idx < len(headers) and headers[idx] else f"col_{idx+1}"
            data[key] = val
        return data

    # 1) Intento con selectors provistos (parte scrollable)
    try:
        headers_main, values_main = _read_part(
            headers_selectors=[header_sel],
            row_selector=row_sel,
            cell_selectors=[cell_sel, "td .cell", "td"],
        )
    except Exception:
        headers_main, values_main = [], []

    data_main = _pair(headers_main, values_main) if (headers_main or values_main) else {}

    # 2) Parte fija ElementUI (columna "Marca de tiempo" suele estar aquí)
    headers_fixed, values_fixed = [], []
    try:
        headers_fixed, values_fixed = _read_part(
            headers_selectors=[
                ".el-table__fixed-header-wrapper th div span div",
                ".el-table__fixed-header-wrapper th .cell",
                ".el-table__fixed-header-wrapper th",
            ],
            row_selector=".el-table__fixed-body-wrapper tbody tr",
            cell_selectors=["td .cell", "td"],
        )
    except Exception:
        pass
    data_fixed = _pair(headers_fixed, values_fixed) if (headers_fixed or values_fixed) else {}

    # 3) Fallbacks típicos ElementUI si lo anterior no trajo nada útil
    if not (data_fixed or data_main) or not any((v or "").strip() for v in list(data_fixed.values()) + list(data_main.values())):
        try:
            headers_main, values_main = _read_part(
                headers_selectors=[
                    ".el-table__header-wrapper th div span div",
                    ".el-table__header-wrapper th .cell",
                    ".el-table__header-wrapper th",
                ],
                row_selector=".el-table__body-wrapper tbody tr",
                cell_selectors=["td .cell", "td"],
            )
            data_main = _pair(headers_main, values_main)
        except Exception:
            pass
        try:
            headers_fixed, values_fixed = _read_part(
                headers_selectors=[
                    ".el-table__fixed-header-wrapper th div span div",
                    ".el-table__fixed-header-wrapper th .cell",
                    ".el-table__fixed-header-wrapper th",
                ],
                row_selector=".el-table__fixed-body-wrapper tbody tr",
                cell_selectors=["td .cell", "td"],
            )
            data_fixed = _pair(headers_fixed, values_fixed)
        except Exception:
            pass

    # Unir (fixed primero para que quede al inicio en headers)
    headers: list[str] = []
    seen_norm: set[str] = set()
    for h in headers_fixed + headers_main:
        nh = _normalize_header_key(h)
        if not nh or nh in seen_norm:
            continue
        seen_norm.add(nh)
        headers.append(h)

    data: dict[str, str] = {}
    data.update(data_fixed)
    existing_norm = {_normalize_header_key(x) for x in data.keys()}
    for k, v in data_main.items():
        nk = _normalize_header_key(k)
        if nk and nk not in existing_norm:
            data[k] = v
            existing_norm.add(nk)
        else:
            # si el key ya existe por fixed (o está vacío), no sobrescribir
            continue

    return headers, data


def _normalize_column_name(header_text: str) -> str:
    # column_name SQL-safe (no espacios, no símbolos).
    base = safe_identifier(header_text).lower()
    if not base:
        base = "x"
    return f"c_{base}_{stable_suffix(header_text, length=6)}"


def _upsert_meta_monitor(
    conn: sqlite3.Connection,
    *,
    monitor_key: str,
    monitor_name: str,
    table_name: str,
    seen_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO meta_monitors (monitor_key, monitor_name, table_name, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(monitor_key) DO UPDATE SET
            monitor_name=excluded.monitor_name,
            table_name=excluded.table_name,
            last_seen_at=excluded.last_seen_at
        """,
        (monitor_key, monitor_name, table_name, seen_at),
    )


def _upsert_meta_columns(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    header_texts: list[str],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for h in header_texts:
        col = _normalize_column_name(h)
        mapping[h] = col
        conn.execute(
            """
            INSERT INTO meta_columns (table_name, header_text, column_name)
            VALUES (?, ?, ?)
            ON CONFLICT(table_name, header_text) DO UPDATE SET
                column_name=excluded.column_name
            """,
            (table_name, h, col),
        )
    return mapping


def _insert_row(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    captured_at: str,
    row_timestamp: str | None,
    columns: dict[str, str],
    values_by_header: dict[str, str],
) -> None:
    cols: list[str] = ["captured_at", "row_timestamp"]
    params: list[object] = [captured_at, row_timestamp]

    for header_text, value in values_by_header.items():
        col = columns.get(header_text)
        if not col:
            continue
        cols.append(col)
        params.append(value)

    placeholders = ",".join(["?"] * len(cols))
    sql_cols = ",".join(cols)
    conn.execute(
        f"INSERT INTO {table_name} ({sql_cols}) VALUES ({placeholders})",
        params,
    )


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    inspection_url = os.getenv("VALUES_INSPECTION_URL")
    if not inspection_url:
        raise SystemExit("Falta VALUES_INSPECTION_URL en .env")

    table_sel = os.getenv("VALUES_SEL_HISTORY_TABLE") or "#maxSeld div.el-table"
    header_sel = (
        os.getenv("VALUES_SEL_HISTORY_HEADER")
        or ".el-table__header-wrapper th div span div, .el-table__header-wrapper th .cell, .el-table__header-wrapper th"
    )
    row_sel = os.getenv("VALUES_SEL_HISTORY_ROW") or ".el-table__body-wrapper tbody tr"
    cell_sel = os.getenv("VALUES_SEL_HISTORY_CELLS") or "td .cell"

    headless = env_flag("HEADLESS", True)
    turbo = env_flag("VALUES_TURBO", False)
    profile = env_flag("VALUES_PROFILE", False)
    use_device_list = env_flag("VALUES_USE_DEVICE_LIST", turbo)
    reset_every_raw = (os.getenv("VALUES_RESET_EVERY") or "").strip()
    try:
        reset_every = int(reset_every_raw) if reset_every_raw else 0
    except Exception:
        reset_every = 0
    tree_max_steps_raw = (os.getenv("VALUES_TREE_MAX_STEPS") or "").strip()
    try:
        tree_max_steps = int(tree_max_steps_raw) if tree_max_steps_raw else None
    except Exception:
        tree_max_steps = None
    storage_dir = base_dir / "storage"
    storage_state_path = storage_dir / "values.json"
    monitors_path = storage_dir / "values-monitors.json"
    if not storage_state_path.exists():
        raise SystemExit("No existe storage/values.json. Ejecuta primero: python values_login.py")
    if not monitors_path.exists():
        raise SystemExit(
            "No existe storage/values-monitors.json. Ejecuta primero: python values_discover_monitors.py"
        )

    monitors_raw = read_json(monitors_path)
    monitors: list[MonitorRef] = [MonitorRef(**m) for m in monitors_raw.get("monitors", [])]
    if not monitors:
        raise SystemExit("No hay monitores en storage/values-monitors.json")

    limit_raw = (os.getenv("VALUES_LIMIT_MONITORS") or "").strip()
    if limit_raw:
        try:
            limit_n = int(limit_raw)
        except Exception:
            limit_n = 0
        if limit_n > 0:
            monitors = monitors[:limit_n]
            print(f"VALUES_LIMIT_MONITORS={limit_n} -> se procesarán {len(monitors)}", flush=True)

    default_history_icon_selectors = [
        "#maxSeld > div > div.seft-log-newTop > div.cer-Dev > i.el-tooltip.iconfont.eb-fs20.icon-a-mingchenglishirizhi3",
        "xpath=//*[@id='maxSeld']/div/div[1]/div[2]/i[5]",
        "i.icon-a-mingchenglishirizhi3",
    ]
    default_history_ready_selectors = [
        "#maxSeld div.el-table",
        "div.el-table",
    ]
    history_icon_selectors = _parse_selector_list(
        os.getenv("VALUES_SEL_HISTORY_ICON") or "",
        default_history_icon_selectors,
    )
    history_ready_selectors = _parse_selector_list(
        os.getenv("VALUES_SEL_HISTORY_READY") or "",
        default_history_ready_selectors,
    )

    debug_dir = storage_dir / "values-scrape"
    debug_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_db(base_dir)
    ensure_meta_tables(conn)
    conn.commit()

    with sync_playwright() as p:
        browser = launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=str(storage_state_path))

        fast_mode = env_flag("VALUES_FAST", False)
        if fast_mode:
            print("VALUES_FAST=1: bloqueando images/fonts/media para acelerar", flush=True)

            def _route_fast(route, request):
                try:
                    rtype = (request.resource_type or "").lower()
                    if rtype in {"image", "media", "font"}:
                        return route.abort()

                    url = (request.url or "").lower()
                    if any(ext in url for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".woff", ".woff2", ".ttf", ".mp4", ".webm"]):
                        return route.abort()
                except Exception:
                    pass
                return route.continue_()

            try:
                context.route("**/*", _route_fast)
            except Exception:
                pass

        page = context.new_page()
        default_timeout_ms = _env_int("VALUES_DEFAULT_TIMEOUT_MS", 30_000)
        nav_timeout_ms = _env_int("VALUES_NAV_TIMEOUT_MS", 60_000)
        page.set_default_timeout(default_timeout_ms)
        page.set_default_navigation_timeout(nav_timeout_ms)

        try:
            # Ir a inspección y preparar árbol
            page.goto(inspection_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            values_login_if_needed(page)
            if page.url != inspection_url:
                page.goto(inspection_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            values_open_inspection_from_menu(page)

            _ensure_energy_storage_tree_open(page)
            tree = _get_tree_root(page)
            tree.wait_for(state="attached", timeout=60_000)

            elementui_header_map: dict[str, tuple[str, bool]] | None = None

            if turbo:
                print(
                    "VALUES_TURBO=1: evitando reset pesado por monitor (solo resetea en fallo)",
                    flush=True,
                )
                if use_device_list:
                    print(
                        "VALUES_USE_DEVICE_LIST=1: abriendo monitores desde la lista (búsqueda) para acelerar",
                        flush=True,
                    )
                if reset_every > 0:
                    print(f"VALUES_RESET_EVERY={reset_every}: forzará reset pesado cada N monitores", flush=True)
                if tree_max_steps is not None:
                    print(f"VALUES_TREE_MAX_STEPS={tree_max_steps}: override max_steps búsqueda árbol", flush=True)

            for idx, mon in enumerate(monitors, start=1):
                mon_key = mon.external_id or mon.name
                table_name = monitor_table_name(mon_key)
                t0 = time.perf_counter()
                print(f"[{idx}/{len(monitors)}] {mon.name}", flush=True)

                try:
                    # En turbo, intentar el flujo rápido y, si falla, hacer reset pesado 1 vez.
                    max_attempts = 2 if turbo else 1
                    last_err: Exception | None = None

                    headers: list[str] = []
                    row_data: dict[str, str] = {}
                    filtered: dict[str, str | None] = {}

                    for attempt in range(1, max_attempts + 1):
                        try:
                            t_attempt0 = time.perf_counter()
                            t_select = 0.0
                            t_detail = 0.0
                            t_history = 0.0
                            t_ready = 0.0
                            t_extract = 0.0

                            # Si venimos de un fallo, o forzamos reset periódico, reconstruir árbol.
                            if attempt > 1 or (turbo and reset_every > 0 and idx % reset_every == 0):
                                tree = _reset_to_inspection_for_next(page, inspection_url)

                            # Seleccionar monitor en el árbol
                            max_steps = tree_max_steps if tree_max_steps is not None else 120
                            t1 = time.perf_counter()
                            opened_from_list = False
                            if use_device_list:
                                # Si ya estamos en la lista, no forzar retorno (evita coste duplicado).
                                try:
                                    in_list = (
                                        page.locator(
                                            "input[placeholder*='PN or SN or device name'], input[placeholder*='device name']"
                                        ).count()
                                        > 0
                                    )
                                except Exception:
                                    in_list = False
                                if not in_list:
                                    try:
                                        _return_to_device_list(page, inspection_url)
                                    except Exception:
                                        pass
                                try:
                                    _open_detail_from_device_list(page, mon.name)
                                    opened_from_list = True
                                except Exception:
                                    opened_from_list = False

                            if not opened_from_list:
                                # Refrescar el árbol en caso de que el DOM haya cambiado.
                                tree = _get_tree_root(page)
                                try:
                                    tree.wait_for(state="attached", timeout=20_000)
                                except Exception:
                                    pass

                                node = _find_monitor_node_with_scroll(page, tree, mon.name, max_steps=max_steps)
                                if node is None:
                                    try:
                                        _expand_energy_storage(page)
                                    except Exception:
                                        pass
                                    # Si expandimos, puede ayudar reiniciar hint de scroll
                                    try:
                                        _navhist.LAST_TREE_SCROLL_TOP = 0
                                    except Exception:
                                        pass
                                    node = _find_monitor_node_with_scroll(page, tree, mon.name, max_steps=max_steps)
                                if node is None:
                                    raise PlaywrightTimeoutError(f"Monitor no encontrado en árbol: {mon.name}")

                                try:
                                    content = node.locator(
                                        "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
                                    ).first
                                    content.scroll_into_view_if_needed(timeout=15_000)
                                    content.click(timeout=15_000)
                                except Exception:
                                    node.click(timeout=15_000, force=True)

                            t_select = time.perf_counter() - t1

                            # Recordar posición del scroll del árbol (reduce scroll scan tras resets)
                            if not opened_from_list:
                                try:
                                    scroll_el = _tree_get_scroll_el(tree)
                                    _navhist.LAST_TREE_SCROLL_TOP = int(
                                        _tree_scroll_state(page, scroll_el).get("top", 0) or 0
                                    )
                                except Exception:
                                    pass

                            # Abrir detalle; si no abre desde el árbol, fallback: tabla + search
                            t2 = time.perf_counter()
                            try:
                                page.locator("#maxSeld").wait_for(state="visible", timeout=8_000)
                            except Exception:
                                _return_to_device_list(page, inspection_url)
                                _open_detail_from_device_list(page, mon.name)
                                page.locator("#maxSeld").wait_for(state="visible", timeout=25_000)

                            t_detail = time.perf_counter() - t2

                            # Abrir Historial
                            t3 = time.perf_counter()
                            clicked = False
                            for _ in range(4):
                                if _click_first_fast(page, history_icon_selectors, timeout_ms=6_000):
                                    clicked = True
                                    break
                                page.wait_for_timeout(500)
                            if not clicked:
                                raise PlaywrightTimeoutError("No se encontró el icono de Historial")

                            t_history = time.perf_counter() - t3

                            # Esperar a que el panel de Historial esté realmente listo.
                            t4 = time.perf_counter()
                            ready = False
                            for sel in history_ready_selectors:
                                try:
                                    page.locator(sel).first.wait_for(state="visible", timeout=20_000)
                                    ready = True
                                    break
                                except Exception:
                                    continue
                            if not ready:
                                # Como fallback, esperar a headers típicos del historial.
                                _wait_history_headers(page, timeout_ms=20_000)
                            t_ready = time.perf_counter() - t4

                            # Resolver cuál de las tablas dentro de #maxSeld corresponde al Historial.
                            hist_table = _resolve_history_table_locator(page, base_sel=table_sel)
                            try:
                                hist_table.wait_for(state="visible", timeout=20_000)
                            except Exception:
                                pass

                            # Extracción primaria (rápida + alineada): por clases de columna ElementUI.
                            t5 = time.perf_counter()
                            try:
                                # Esperar a que haya al menos una fila renderizada.
                                hist_table.locator(".el-table__body-wrapper tbody tr").first.wait_for(state="visible", timeout=20_000)
                            except Exception:
                                try:
                                    hist_table.locator(".el-table__fixed-body-wrapper tbody tr").first.wait_for(state="visible", timeout=60_000)
                                except Exception:
                                    # Si no hay filas visibles, registrar NO DATA y salir sin reintentos
                                    print(f"  NO DATA: {mon.name} (tabla vacía, sin reintentos)", flush=True)
                                    filtered = {k: None for k in VALUES_COLUMNS_TO_EXTRACT}
                                    last_err = None
                                    break
                            if elementui_header_map is None:
                                elementui_header_map = _build_elementui_header_map(hist_table)
                            filtered = _extract_first_row_fixed_columns_elementui_cached(
                                page,
                                table=hist_table,
                                fixed_columns=VALUES_COLUMNS_TO_EXTRACT,
                                header_map=elementui_header_map,
                            )

                            # Sanity-check: si casi todo viene vacío, probablemente elegimos mal la tabla
                            # o el header_map quedó cacheado de otra vista. Recalcular 1 vez.
                            non_empty_now = sum(
                                1 for v in filtered.values() if v is not None and str(v).strip() != ""
                            )
                            # NUEVO: Si la tabla está vacía, registrar NO DATA y continuar sin reintentos
                            if non_empty_now == 0:
                                print(f"  NO DATA: {mon.name} (tabla vacía, sin reintentos)", flush=True)
                                filtered = {k: None for k in VALUES_COLUMNS_TO_EXTRACT}
                                last_err = None
                                break
                            if non_empty_now <= 2:
                                elementui_header_map = _build_elementui_header_map(hist_table)
                                filtered = _extract_first_row_fixed_columns_elementui_cached(
                                    page,
                                    table=hist_table,
                                    fixed_columns=VALUES_COLUMNS_TO_EXTRACT,
                                    header_map=elementui_header_map,
                                )
                            t_extract_fixed = time.perf_counter() - t5

                            # Fallback opcional: método antiguo (más lento) solo si faltan datos críticos.
                            row_data = {}
                            headers = []
                            row_timestamp_candidate = (filtered.get("Marca de Tiempo") or "").strip()
                            need_fallback = (not row_timestamp_candidate) or (non_empty_now < max(5, int(len(VALUES_COLUMNS_TO_EXTRACT) * 0.35)))

                            t_extract_fallback = 0.0
                            if need_fallback and not turbo:
                                t6 = time.perf_counter()
                                headers, row_data = _extract_first_row(
                                    page,
                                    table_sel=table_sel,
                                    header_sel=header_sel,
                                    row_sel=row_sel,
                                    cell_sel=cell_sel,
                                )
                                t_extract_fallback = time.perf_counter() - t6

                                filtered_from_row_data = _filter_row_to_fixed_columns(
                                    row_data,
                                    fixed_columns=VALUES_COLUMNS_TO_EXTRACT,
                                )
                                for k, v in filtered_from_row_data.items():
                                    if filtered.get(k) is None and v is not None and str(v).strip() != "":
                                        filtered[k] = v

                            t_extract = t_extract_fixed + t_extract_fallback

                            if profile:
                                dt_attempt = time.perf_counter() - t_attempt0
                                extra = ""
                                if t_extract_fallback > 0:
                                    extra = f" | fallback_extract={t_extract_fallback:.1f}s"
                                print(
                                    "  PERF"
                                    + f" | select_tree={t_select:.1f}s"
                                    + f" | load_detail={t_detail:.1f}s"
                                    + f" | click_history={t_history:.1f}s"
                                    + f" | wait_ready={t_ready:.1f}s"
                                    + f" | extract_fixed={t_extract_fixed:.1f}s"
                                    + extra
                                    + f" | attempt_total={dt_attempt:.1f}s",
                                    flush=True,
                                )

                            # Si llegamos acá, el intento fue exitoso.
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            if turbo and attempt < max_attempts:
                                print(f"  WARN: fallo intento {attempt}/{max_attempts}: {e} -> reset y reintento", flush=True)
                                continue
                            raise

                    if last_err is not None:
                        raise last_err

                    captured_at = _utc_now_iso()
                    row_timestamp = (filtered.get("Marca de Tiempo") or "").strip() or None
                    if not row_timestamp:
                        # Si existe una cabecera que parezca timestamp, usamos su valor.
                        for k in list(row_data.keys()):
                            if k.strip().lower() in {"timestamp", "time", "datetime", "date", "marca de tiempo"}:
                                row_timestamp = row_data.get(k) or None
                                break

                    # Asegurar que la columna "Marca de Tiempo" quede poblada.
                    if (
                        filtered.get("Marca de Tiempo") is None
                        or str(filtered.get("Marca de Tiempo") or "").strip() == ""
                    ) and row_timestamp:
                        filtered["Marca de Tiempo"] = row_timestamp

                    columns_map = _upsert_meta_columns(
                        conn,
                        table_name=table_name,
                        header_texts=VALUES_COLUMNS_TO_EXTRACT,
                    )
                    ensure_monitor_table(conn, table_name=table_name, column_names=list(columns_map.values()))
                    _upsert_meta_monitor(
                        conn,
                        monitor_key=mon_key,
                        monitor_name=mon.name,
                        table_name=table_name,
                        seen_at=captured_at,
                    )
                    _insert_row(
                        conn,
                        table_name=table_name,
                        captured_at=captured_at,
                        row_timestamp=row_timestamp,
                        columns=columns_map,
                        values_by_header={k: ("" if v is None else str(v)) for k, v in filtered.items()},
                    )
                    conn.commit()

                    # Confirmación + métrica de completitud
                    non_empty = sum(1 for v in filtered.values() if v is not None and str(v).strip() != "")
                    print(
                        "  Dato enviado correctamente a la Base de datos"
                        + f" | tabla={table_name}"
                        + f" | marca_de_tiempo={(row_timestamp or '')}"
                        + f" | columnas_no_vacias={non_empty}/{len(VALUES_COLUMNS_TO_EXTRACT)}",
                        flush=True,
                    )

                    # Volver a lista/árbol (debe ser recuperable: el dato ya se insertó)
                    try:
                        if turbo:
                            # En turbo: evitar reset pesado siempre; mantener el estado/scroll del árbol.
                            _return_to_device_list(page, inspection_url)
                            if not use_device_list:
                                try:
                                    _ensure_energy_storage_tree_open(page, wait_monitors=False)
                                except Exception:
                                    pass
                                tree = _get_tree_root(page)
                                tree.wait_for(state="attached", timeout=60_000)
                        else:
                            tree = _reset_to_inspection_for_next(page, inspection_url)
                    except Exception as e:
                        # Recuperación: reset pesado para dejar listo el siguiente monitor
                        print(f"  WARN: no se pudo volver a lista/árbol: {e} -> reset", flush=True)
                        tree = _reset_to_inspection_for_next(page, inspection_url)

                    dt = time.perf_counter() - t0
                    print(f"  Tiempo monitor: {dt:.1f}s", flush=True)
                except Exception:
                    conn.rollback()
                    dump_debug(page, debug_dir, f"{safe_identifier(mon.name)}-error")
                    print(f"  ERROR: ver artifacts en {debug_dir}")
                    # Preparar el contexto para que el siguiente monitor no herede un árbol roto.
                    try:
                        tree = _reset_to_inspection_for_next(page, inspection_url)
                    except Exception:
                        try:
                            page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)
                            tree = _get_tree_root(page)
                        except Exception:
                            pass
                    continue

        finally:
            context.close()
            browser.close()
            conn.close()

    print("OK")


if __name__ == "__main__":
    main()

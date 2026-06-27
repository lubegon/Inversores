"""Scraper de voltaje/lecturas desde ShineMonitor.

Este módulo contiene la implementación principal de scraping para ShineMonitor:
- Carga plantas desde `storage/shinemonitor-plants.json`.
- Para cada planta: navega a Device Management, expande el árbol (Inverter -> monitores).
- Abre *Data Details* y lee la última fila disponible.
- Persiste resultados en SQLite (`Voltage  Shinemonitor.sqlite`) y guarda artifacts en `storage/scrape/`.

El script raíz `shinemonitor_scrape_voltage.py` se mantiene como wrapper para
compatibilidad (no mezclar proveedores en la raíz).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Locator, sync_playwright

SHINE_URL = "https://shinemonitor.com/index_en.html?1770834820036"

# Navigation selectors
SELECTOR_PLANTS_TOGGLE = "#headPlos > div.logo-container > div > a"
SELECTOR_PLANTS_LIST = "#plantlist > ul"
SELECTOR_DEVICE_MGMT_TAB = "#plantTab > li:nth-child(4) > a"  # Device Management
SELECTOR_TREE_BOX = "#treeLeftBox"  # árbol (Inverter, monitores, etc.)
SELECTOR_DATA_DETAILS_TAB = "#inverterpab > li:nth-child(5) > a"  # Data Details

# Data selectors
SELECTOR_INV_DETAIL_CONTAINER = "#invDetailCon"
SELECTOR_INV_DETAIL_CUE = "#invDetailCue"  # texto "Sorry,YYYY-MM-DD ..."


COLUMNS = [
    "Timestamp",
    "Battery Voltage(V)",
    "PV Voltage(V)",
    "Inverter Voltage(V)",
    "Batt Current(A)",
    "Charger Current(A)",
    "Charger Power(W)",
    "PLoad(W)",
    "PGrid(W)",
    "work state",
    "rated power(W)",
    "Grid Voltage(V)",
    "PInverter(W)",
    "Accumulated Sell Power(kWh)",
    "Accumulated Load Power(kWh)",
    "Accumulated Self_Use Power(kWh)",
    "charger work enable",
    "Accumulated PV Power(kWh)",
]


@dataclass(frozen=True)
class PlantRef:
    plant_id: str
    name: str | None


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip().replace("_", ""))
    except Exception:
        return default


def _browser_choice() -> str:
    return (os.getenv("BROWSER") or "chromium").strip().lower()


def _launch_browser(p, headless: bool):
    choice = _browser_choice()
    use_edge = choice in {"edge", "msedge"}

    try:
        if use_edge:
            return p.chromium.launch(headless=headless, channel="msedge")
        return p.chromium.launch(headless=headless)
    except PlaywrightError:
        if use_edge:
            return p.chromium.launch(headless=headless)
        raise


def _login_if_needed(page, user: str, password: str) -> None:
    if page.locator("#loginusr > input").is_visible():
        page.locator("#loginusr > input").fill(user)
        page.locator("#mypassword").fill(password)
        page.locator("#loginbtn").click()
        try:
            page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)


def _open_plants_dropdown(page) -> None:
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
            toggle.click(timeout=10_000, force=True)

        try:
            page.wait_for_selector("#plantlist", state="visible", timeout=10_000)
            break
        except Exception:
            if attempt == 3:
                raise

    page.wait_for_selector(SELECTOR_PLANTS_LIST, state="attached", timeout=30_000)
    page.wait_for_timeout(200)


def _select_plant(page, plant_id: str) -> str | None:
    _open_plants_dropdown(page)

    plant_anchor = page.locator(f"xpath=//*[@id='plant_{plant_id}']/a")
    plant_anchor.wait_for(state="visible", timeout=30_000)

    plant_name = " ".join(plant_anchor.inner_text().split()).strip() or None

    plant_anchor.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    page.wait_for_timeout(1200)
    return plant_name


def _click_device_management(page) -> None:
    tab = page.locator(SELECTOR_DEVICE_MGMT_TAB)
    tab.wait_for(state="visible", timeout=30_000)
    tab.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass

    page.wait_for_selector(SELECTOR_TREE_BOX, state="visible", timeout=30_000)
    page.wait_for_timeout(600)


def _dump_debug(page, run_dir: Path, name: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _tree_is_empty(tree: Locator) -> bool:
    try:
        # "Vacío" = no hay nodos/anchors renderizados (suele pasar si el click/selección no pegó)
        if tree.locator("a.jstree-anchor").count() > 0:
            return False
        if tree.locator("li").count() > 0:
            return False
        # Algunos layouts muestran el contenedor pero sin children aún.
        txt = (tree.inner_text() or "").strip()
        return not bool(txt)
    except Exception:
        return False


def _select_plant_and_load_tree(
    page,
    *,
    plant: PlantRef,
    run_dir: Path,
    timeout_ms: int = 30_000,
    retries: int = 1,
) -> tuple[str | None, Locator]:
    """Selecciona planta -> Device Management -> árbol visible.

    Reintenta SOLO si el árbol no carga (timeout) o queda vacío.
    """

    last_exc: Exception | None = None
    plant_name: str | None = None

    for attempt in range(1, max(0, retries) + 2):
        try:
            plant_name = _select_plant(page, plant_id=plant.plant_id) or plant.name

            page.screenshot(
                path=str(run_dir / f"{plant.plant_id}-01-selected-a{attempt}.png"),
                full_page=True,
            )

            _click_device_management(page)

            tree = _ensure_tree_loaded(
                page,
                timeout_ms=timeout_ms,
                retries=2,
                run_dir=run_dir,
                debug_name=f"{plant.plant_id}-tree-timeout-a{attempt}",
            )
            if _tree_is_empty(tree):
                raise RuntimeError("TREE_EMPTY")

            return plant_name, tree
        except Exception as e:
            last_exc = e
            is_retryable = isinstance(e, PlaywrightTimeoutError) or (str(e).strip() == "TREE_EMPTY")
            if attempt >= max(0, retries) + 1 or not is_retryable:
                raise

            try:
                print(
                    f"  [RETRY] Plant {plant.plant_id}: reintentando selección/árbol (attempt {attempt+1})",
                    flush=True,
                )
            except Exception:
                pass
            _dump_debug(page, run_dir, f"{plant.plant_id}-tree-retry-a{attempt}")
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass

    # No debería llegar acá.
    raise last_exc or RuntimeError("No se pudo seleccionar planta/cargar árbol")

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


def _ensure_tree_loaded(
    page,
    *,
    timeout_ms: int = 30_000,
    retries: int = 2,
    run_dir: Path | None = None,
    debug_name: str = "tree-not-visible",
) -> Locator:
    # Selector principal + fallbacks (por si el sitio cambia IDs/clases)
    selectors = [
        SELECTOR_TREE_BOX,
        "#treeBox",
        "#treeLeft",
        "div#treeLeftBox",
        "div#treeBox",
    ]

    last_exc: Exception | None = None
    for attempt in range(1, max(1, retries) + 2):
        for sel in selectors:
            try:
                tree = page.locator(sel)
                tree.wait_for(state="visible", timeout=timeout_ms)
                return tree
            except Exception as e:
                last_exc = e

        # Best-effort: si el árbol no aparece, quizá no quedó en el tab correcto.
        try:
            _click_device_management(page)
        except Exception:
            pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass

        if attempt == max(1, retries) + 1 and run_dir is not None:
            _dump_debug(page, run_dir, debug_name)

    raise PlaywrightTimeoutError(
        f"No se pudo cargar el árbol (selectors={selectors}). Último error: {last_exc}"
    )


def _open_node_if_needed(node_li: Locator) -> None:
    klass = (node_li.get_attribute("class") or "").lower()
    if "jstree-open" in klass:
        return

    ocl = node_li.locator("xpath=./i[contains(@class,'jstree-ocl')]")
    if ocl.count() > 0:
        ocl.first.click()
        node_li.page.wait_for_timeout(300)


def _expand_tree(tree: Locator, *, rounds: int = 3, max_nodes_per_round: int = 50) -> None:
    # Algunos árboles no renderizan los nodos hijos hasta abrirlos.
    # Abrimos nodos cerrados para que aparezcan todos los "Inverter" (en plantas con 2+ dataloggers).
    for _ in range(max(1, rounds)):
        closed = tree.locator("li.jstree-closed")
        count = closed.count()
        if count == 0:
            return
        for i in range(min(count, max_nodes_per_round)):
            _open_node_if_needed(closed.nth(i))


def _collect_inverters_and_device_anchors(tree: Locator) -> tuple[int, list[Locator]]:
    # Puede haber múltiples nodos "Inverter" (uno por datalogger). Unimos todos los monitores.
    _expand_tree(tree)

    inverter_anchors = tree.locator("a.jstree-anchor", has_text="Inverter")
    inv_count = inverter_anchors.count()
    if inv_count == 0:
        return 0, []

    results: list[Locator] = []
    seen: set[str] = set()
    for i in range(inv_count):
        inverter_li = inverter_anchors.nth(i).locator("xpath=ancestor::li[1]")
        _open_node_if_needed(inverter_li)
        for a in _collect_device_anchors_under(inverter_li):
            key = (a.get_attribute("id") or "").strip()
            if not key:
                key = "name:" + (" ".join((a.inner_text() or "").split()).strip() or "unknown")
            if key in seen:
                continue
            seen.add(key)
            results.append(a)

    return inv_count, results


def _collect_device_anchors_under(li: Locator) -> list[Locator]:
    anchors = li.locator("xpath=.//ul//a[contains(@class,'jstree-anchor')]")
    results: list[Locator] = []
    for i in range(anchors.count()):
        a = anchors.nth(i)
        text = " ".join(a.inner_text().split()).strip()
        if not text:
            continue
        if text.lower() == "inverter":
            continue
        results.append(a)
    return results


def _click_data_details(
    page,
    *,
    timeout_ms: int = 30_000,
    run_dir: Path | None = None,
    debug_name: str = "no-data-details-tab",
) -> bool:
    """Intenta abrir la pestaña "Data Details".

    En algunas plantas/equipos el layout cambia y la pestaña puede no existir.
    En ese caso devolvemos False (y opcionalmente dumpeamos artifacts) para que
    el caller pueda continuar sin abortar toda la corrida.
    """

    label = re.compile(r"Data\s*Details", re.IGNORECASE)
    candidates: list[Locator] = [
        page.locator(SELECTOR_DATA_DETAILS_TAB),
        page.locator("#inverterpab a", has_text=label),
        page.locator("a", has_text=label),
    ]

    last_exc: Exception | None = None
    per_try_timeout = max(3_000, int(timeout_ms / 3))
    for _ in range(3):
        for cand in candidates:
            try:
                if cand.count() == 0:
                    continue
                tab = cand.first
                tab.wait_for(state="visible", timeout=per_try_timeout)
                try:
                    tab.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass

                try:
                    tab.click(timeout=5_000)
                except Exception:
                    tab.click(timeout=5_000, force=True)

                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                page.wait_for_timeout(700)
                return True
            except Exception as e:
                last_exc = e

        try:
            page.wait_for_timeout(500)
        except Exception:
            pass

    if run_dir is not None:
        _dump_debug(page, run_dir, debug_name)
    return False


def _find_device_anchor(
    anchors: list[Locator], *, device_key: str, device_name: str
) -> Locator | None:
    # Preferimos match por id estable
    for a in anchors:
        try:
            if _device_key(a) == device_key:
                return a
        except Exception:
            continue

    # Fallback por nombre visible (menos estable)
    dn = (device_name or "").strip().lower()
    if dn:
        for a in anchors:
            try:
                txt = " ".join((a.inner_text() or "").split()).strip().lower()
            except Exception:
                txt = ""
            if txt and txt == dn:
                return a
    return None


def _extract_no_data_message(page) -> tuple[str | None, str | None]:
    cue = page.locator(SELECTOR_INV_DETAIL_CUE)
    if cue.count() == 0:
        return None, None

    try:
        if not cue.is_visible():
            return None, None
    except Exception:
        return None, None

    text = " ".join((cue.inner_text() or "").split()).strip()
    if not text:
        return "NO_DATA", ""

    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        return "NO_DATA", date_match.group(1)

    return "NO_DATA", text


def _extract_latest_row(page, *, timeout_ms: int = 10_000) -> dict[str, str] | None:
    container = page.locator(SELECTOR_INV_DETAIL_CONTAINER)
    if container.count() == 0:
        return None

    # esperar a que exista una tabla (si hay datos)
    try:
        container.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return None

    # Primera fila del tbody dentro del contenedor
    row = container.locator("table tbody tr").first
    try:
        # En cargas lentas, el contenedor aparece antes que las filas.
        row.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        if row.count() == 0:
            return None

    cells = row.locator("td")
    if cells.count() == 0:
        return None

    values: list[str] = []
    for i in range(cells.count()):
        values.append(" ".join((cells.nth(i).inner_text() or "").split()).strip())

    # Mapear por posición a las columnas esperadas (si hay menos celdas, se rellena)
    mapped: dict[str, str] = {}
    for idx, col in enumerate(COLUMNS):
        mapped[col] = values[idx] if idx < len(values) else ""

    return mapped


def _safe_identifier(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "x"


def _device_key(anchor: Locator) -> str:
    # Preferimos un ID estable si existe (ej: DEV$#B142..._anchor)
    raw = (anchor.get_attribute("id") or "").strip()
    if raw:
        return raw
    # Fallback: nombre visible (menos estable)
    return "name:" + (" ".join((anchor.inner_text() or "").split()).strip() or "unknown")


def _device_internal_table(plant_id: str, device_key: str) -> str:
    # Tabla interna estable por device_key (usada solo como fallback/diagnóstico).
    digest = hashlib.sha1(device_key.encode("utf-8")).hexdigest()[:10]
    return f"device_{plant_id}_{digest}"


def _db_path(base_dir: Path) -> Path:
    # "Voltage  Shinemonitor" pedido por el usuario (nombre del archivo)
    # Usamos extensión sqlite para claridad.
    return base_dir / "Voltage  Shinemonitor.sqlite"


def _ensure_meta_tables(conn: sqlite3.Connection) -> None:
        conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_plants (
                    plant_id TEXT PRIMARY KEY,
                    plant_name TEXT,
                    last_seen_at TEXT NOT NULL
                )
                """
        )

        conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_devices (
                    device_key TEXT PRIMARY KEY,
                    plant_id TEXT NOT NULL,
                    device_name TEXT,
                    table_name TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
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


def _ensure_device_table(conn: sqlite3.Connection, table: str) -> None:
        # Una tabla por dispositivo (monitor) bajo Inverter
    cols_sql = ",\n      ".join([f'"{c}" TEXT' for c in COLUMNS])

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          captured_at TEXT NOT NULL,
          plant_id TEXT NOT NULL,
          plant_name TEXT,
          device_name TEXT,
                    device_key TEXT,
          status TEXT NOT NULL,
          status_detail TEXT,
          {cols_sql}
        )
        """
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone()
    return row is not None


def _get_device_table(conn: sqlite3.Connection, device_key: str) -> str | None:
    row = conn.execute(
        "SELECT table_name FROM meta_devices WHERE device_key=?", (device_key,)
    ).fetchone()
    return str(row[0]) if row else None


def _claim_friendly_table_name(
    conn: sqlite3.Connection,
    *,
    desired: str,
    device_key: str,
    plant_id: str,
) -> str:
    # Evita colisiones: si el nombre existe y pertenece al mismo device_key -> ok.
    # Si existe pero no sabemos a quién pertenece, agregamos sufijo.
    # Nota: el usuario pidió "sin ID"; solo agregamos sufijo si es estrictamente necesario.
    name = desired
    for attempt in range(1, 50):
        if not _table_exists(conn, name):
            return name

        row = conn.execute(
            "SELECT device_key FROM meta_devices WHERE table_name=? LIMIT 1", (name,)
        ).fetchone()
        if row and str(row[0]) == device_key:
            return name

        suffix = f"_{attempt}"
        name = (desired + suffix)[:200]

    # último recurso: usar tabla interna estable
    return _device_internal_table(plant_id, device_key)


def _desired_table_name(plant_name: str | None, device_name: str, device_count: int) -> str:
    plant_part = _safe_identifier(plant_name or "plant")
    if device_count <= 1:
        return plant_part
    dev_part = _safe_identifier(device_name or "device")
    return f"{plant_part}_{dev_part}"


def _upsert_meta_plant(conn: sqlite3.Connection, plant_id: str, plant_name: str | None, now: str) -> None:
    conn.execute(
        """
        INSERT INTO meta_plants (plant_id, plant_name, last_seen_at)
        VALUES (?, ?, ?)
        ON CONFLICT(plant_id) DO UPDATE SET
          plant_name=excluded.plant_name,
          last_seen_at=excluded.last_seen_at
        """,
        (plant_id, plant_name, now),
    )


def _upsert_meta_device(
    conn: sqlite3.Connection,
    *,
    device_key: str,
    plant_id: str,
    device_name: str | None,
    table_name: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO meta_devices (device_key, plant_id, device_name, table_name, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_key) DO UPDATE SET
          plant_id=excluded.plant_id,
          device_name=excluded.device_name,
          table_name=excluded.table_name,
          last_seen_at=excluded.last_seen_at
        """,
        (device_key, plant_id, device_name, table_name, now),
    )


def _insert_row(
    conn: sqlite3.Connection,
    table: str,
    *,
    captured_at: str,
    plant_id: str,
    plant_name: str | None,
    device_name: str | None,
    device_key: str | None,
    status: str,
    status_detail: str | None,
    data: dict[str, str] | None,
) -> None:
    row: dict[str, Any] = {c: "" for c in COLUMNS}
    if data:
        row.update(data)

    fields = [
        "captured_at",
        "plant_id",
        "plant_name",
        "device_name",
        "device_key",
        "status",
        "status_detail",
        *COLUMNS,
    ]

    values = [
        captured_at,
        plant_id,
        plant_name,
        device_name,
        device_key,
        status,
        status_detail,
        *[row[c] for c in COLUMNS],
    ]

    placeholders = ",".join(["?"] * len(fields))
    quoted_fields = ",".join([f'"{f}"' for f in fields])

    conn.execute(
        f"INSERT INTO "
        f'"{table}" ({quoted_fields}) VALUES ({placeholders})',
        values,
    )


def _insert_plant_event(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
    plant_id: str,
    plant_name: str | None,
    status: str,
    status_detail: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO plant_events (captured_at, plant_id, plant_name, status, status_detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (captured_at, plant_id, plant_name, status, status_detail),
    )


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

    default_timeout_ms = _env_int("SHINE_DEFAULT_TIMEOUT_MS", 30_000)
    nav_timeout_ms = _env_int("SHINE_NAV_TIMEOUT_MS", 60_000)

    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    storage_state_path = storage_dir / "shinemonitor.json"

    # scope: uno o todos
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

        # Crear tablas meta/eventos antes de cualquier inserción
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
                # Espera extendida tras cargar la página principal
                page.wait_for_selector("#loginusr > input", timeout=60_000)
                page.wait_for_timeout(5000)  # Espera adicional para asegurar carga completa
                _login_if_needed(page, user=user, password=password)

                for idx, plant in enumerate(plants, start=1):
                    print(f"[{idx}/{len(plants)}] Plant {plant.plant_id}...", flush=True)

                    plant_name: str | None = None
                    try:
                        # Si la sesión se cae en medio del run, re-login best-effort.
                        _login_if_needed(page, user=user, password=password)

                        # Espera extendida tras seleccionar planta
                        page.wait_for_timeout(3000)
                        plant_name, tree = _select_plant_and_load_tree(
                            page,
                            plant=plant,
                            run_dir=run_dir,
                            timeout_ms=60_000,
                            retries=1,
                        )

                        _upsert_meta_plant(conn, plant.plant_id, plant_name, captured_at)
                        conn.commit()
                    except Exception as e:
                        # Registrar el fallo de la planta y seguir con la siguiente.
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

                    inverter_count, device_anchors = _collect_inverters_and_device_anchors(tree)
                    if inverter_count == 0:
                        # requisito: guardar "No hay Inverter"
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
                        # inverter existe pero sin monitores
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
                        # re-seleccionar en cada iteración para evitar locators viejos
                        try:
                            # Espera extendida tras cargar árbol
                            page.wait_for_timeout(2000)
                            tree = _ensure_tree_loaded(
                                page,
                                timeout_ms=60_000,
                                retries=1,
                                run_dir=run_dir,
                                debug_name=f"{plant.plant_id}-tree-reload-{dev_index+1:02d}",
                            )
                            if _tree_is_empty(tree):
                                raise RuntimeError("TREE_EMPTY")
                        except Exception as e:
                            # Reintento SOLO si es timeout / árbol vacío
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
                                        timeout_ms=60_000,
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
                            # Renombrar para cumplir el naming requerido por negocio.
                            # Si desired ya existe y no es del mismo device_key, claim_friendly_table_name habrá ajustado.
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

                        # --- Abrir Data Details (con 1 reintento si no aparece) ---
                        a.click()
                        page.wait_for_timeout(700)
                        opened = _click_data_details(
                            page,
                            timeout_ms=30_000,
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
                                    timeout_ms=60_000,
                                    retries=1,
                                )
                                if plant_name_retry:
                                    plant_name = plant_name_retry

                                _, anchors_retry = _collect_inverters_and_device_anchors(tree_retry)
                                a_retry = _find_device_anchor(
                                    anchors_retry,
                                    device_key=device_key,
                                    device_name=device_name,
                                )
                                if a_retry is not None:
                                    a_retry.click()
                                    page.wait_for_timeout(900)
                                    opened = _click_data_details(
                                        page,
                                        timeout_ms=120_000,
                                        run_dir=run_dir,
                                        debug_name=f"{plant.plant_id}-03-notab-retry-{dev_index+1:02d}",
                                    )
                            except Exception:
                                opened = False

                        if not opened:
                            _insert_row(
                                conn,
                                device_table,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                device_name=device_name,
                                device_key=device_key,
                                status="NO_TAB",
                                status_detail="No se encontró la pestaña 'Data Details' (reintentado)",
                                data=None,
                            )
                            conn.commit()
                            continue

                        status, status_detail = _extract_no_data_message(page)
                        if status == "NO_DATA":
                            # requisito: marcar como "no tiene data" y guardar fecha
                            try:
                                detail_txt = (status_detail or "").strip()
                                if detail_txt:
                                    print(f"    - NO_DATA: {device_name} ({detail_txt})", flush=True)
                                else:
                                    print(f"    - NO_DATA: {device_name}", flush=True)
                            except Exception:
                                pass
                            _insert_row(
                                conn,
                                device_table,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                device_name=device_name,
                                device_key=device_key,
                                status="NO_DATA",
                                status_detail=status_detail,
                                data=None,
                            )
                            # También registrar a nivel planta para auditoría rápida.
                            try:
                                det = f"{device_name}: {status_detail}" if status_detail else device_name
                                _insert_plant_event(
                                    conn,
                                    captured_at=captured_at,
                                    plant_id=plant.plant_id,
                                    plant_name=plant_name,
                                    status="NO_DATA",
                                    status_detail=(det[:500] if det else None),
                                )
                            except Exception:
                                pass
                            conn.commit()
                            page.screenshot(
                                path=str(run_dir / f"{plant.plant_id}-03-nodata-{dev_index+1:02d}.png"),
                                full_page=True,
                            )
                            continue

                        latest = _extract_latest_row(page, timeout_ms=20_000)
                        if latest is None:
                            # Reintentar SOLO para este error: re-seleccionar planta/device y esperar más.
                            try:
                                print(
                                    f"  [RETRY] No se pudo leer invDetailCon; reintentando planta/device...",
                                    flush=True,
                                )
                            except Exception:
                                pass

                            try:
                                plant_name_retry, tree_retry = _select_plant_and_load_tree(
                                    page,
                                    plant=plant,
                                    run_dir=run_dir,
                                    timeout_ms=60_000,
                                    retries=1,
                                )
                                if plant_name_retry:
                                    plant_name = plant_name_retry

                                _, anchors_retry = _collect_inverters_and_device_anchors(tree_retry)
                                a_retry = _find_device_anchor(
                                    anchors_retry,
                                    device_key=device_key,
                                    device_name=device_name,
                                )
                                if a_retry is not None:
                                    a_retry.click()
                                    page.wait_for_timeout(900)
                                    if _click_data_details(
                                        page,
                                        timeout_ms=120_000,
                                        run_dir=run_dir,
                                        debug_name=f"{plant.plant_id}-03-notab-retry2-{dev_index+1:02d}",
                                    ):
                                        status2, status_detail2 = _extract_no_data_message(page)
                                        if status2 == "NO_DATA":
                                            try:
                                                detail_txt = (status_detail2 or "").strip()
                                                if detail_txt:
                                                    print(
                                                        f"    - NO_DATA: {device_name} ({detail_txt})",
                                                        flush=True,
                                                    )
                                                else:
                                                    print(f"    - NO_DATA: {device_name}", flush=True)
                                            except Exception:
                                                pass
                                            _insert_row(
                                                conn,
                                                device_table,
                                                captured_at=captured_at,
                                                plant_id=plant.plant_id,
                                                plant_name=plant_name,
                                                device_name=device_name,
                                                device_key=device_key,
                                                status="NO_DATA",
                                                status_detail=status_detail2,
                                                data=None,
                                            )
                                            try:
                                                det = (
                                                    f"{device_name}: {status_detail2}"
                                                    if status_detail2
                                                    else device_name
                                                )
                                                _insert_plant_event(
                                                    conn,
                                                    captured_at=captured_at,
                                                    plant_id=plant.plant_id,
                                                    plant_name=plant_name,
                                                    status="NO_DATA",
                                                    status_detail=(det[:500] if det else None),
                                                )
                                            except Exception:
                                                pass
                                            conn.commit()
                                            page.screenshot(
                                                path=str(
                                                    run_dir
                                                    / f"{plant.plant_id}-03-nodata-retry-{dev_index+1:02d}.png"
                                                ),
                                                full_page=True,
                                            )
                                            continue

                                        latest = _extract_latest_row(page, timeout_ms=60_000)
                            except Exception:
                                pass

                        if latest is None:
                            _insert_row(
                                conn,
                                device_table,
                                captured_at=captured_at,
                                plant_id=plant.plant_id,
                                plant_name=plant_name,
                                device_name=device_name,
                                device_key=device_key,
                                status="NO_TABLE",
                                status_detail="No se pudo leer la tabla invDetailCon (reintentado)",
                                data=None,
                            )
                            conn.commit()
                            page.screenshot(
                                path=str(run_dir / f"{plant.plant_id}-03-notable-{dev_index+1:02d}.png"),
                                full_page=True,
                            )
                            continue

                        _insert_row(
                            conn,
                            device_table,
                            captured_at=captured_at,
                            plant_id=plant.plant_id,
                            plant_name=plant_name,
                            device_name=device_name,
                            device_key=device_key,
                            status="OK",
                            status_detail=None,
                            data=latest,
                        )
                        conn.commit()

                        page.screenshot(
                            path=str(run_dir / f"{plant.plant_id}-03-ok-{dev_index+1:02d}.png"),
                            full_page=True,
                        )

                context.storage_state(path=str(storage_state_path))

            finally:
                context.close()
                browser.close()

    finally:
        conn.close()

    print(f"DB: {db_path}")
    print("Terminado")


if __name__ == "__main__":
    main()

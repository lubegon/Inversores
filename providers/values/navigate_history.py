"""Navegación Values: inspección -> monitor -> historial (sin extraer tabla).

Objetivo:
- Validar que el flujo puede recorrer TODOS los monitores y abrir la sección
  "Historial" para cada uno.

Entradas:
- `storage/values-monitors.json` (generado por discover)
- `storage/values.json` (storageState generado por login)

Config `.env`:
- `VALUES_INSPECTION_URL`
- `VALUES_ENERGY_STORAGE_LABEL` (opcional)
- `VALUES_SEL_HISTORY_ICON` (opcional; default: "i.icon-a-mingchenglishirizhi3")
- `VALUES_SEL_HISTORY_READY` (opcional; default: "div.el-table")
- `VALUES_LIMIT_MONITORS` (opcional; int)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import (
    MonitorRef,
    RunLogger,
    dump_debug,
    env_flag,
    launch_browser,
    read_json,
    safe_identifier,
    values_login_if_needed,
    values_open_inspection_from_menu,
)


def _wait_tree_loaded(page, *, timeout_ms: int = 45_000) -> None:
    """Espera best-effort a que el árbol termine de cargar."""

    deadline = page.evaluate("() => Date.now()") + int(timeout_ms)
    while True:
        try:
            loading = page.locator(".el-tree-node__loading-icon").count()
        except Exception:
            loading = 0
        if loading == 0:
            return
        now = page.evaluate("() => Date.now()")
        if now >= deadline:
            return
        page.wait_for_timeout(400)


def _expand_energy_storage(page) -> None:
    label = (os.getenv("VALUES_ENERGY_STORAGE_LABEL") or "").strip()
    # El label suele mostrarse truncado con ellipsis ("Almacenamiento de e...")
    # así que usamos match parcial.
    labels = [l.strip() for l in [label, "Almacenamiento", "Energy storage", "Energy"] if l.strip()]

    left = page.locator("div.device-detail-tree-box-left").first
    left.wait_for(state="attached", timeout=60_000)

    nodes = left.locator(
        "div.el-tree span.dev_lable2, div.el-tree span.dev_lable1, div.el-tree span.el-tree-node__label"
    )
    nodes.first.wait_for(state="attached", timeout=60_000)

    for text in labels:
        node = nodes.filter(has_text=text)
        if node.count() == 0:
            continue

        # Nunca tocar/seleccionar "Mi equipo".
        try:
            txt = (node.first.text_content() or "").strip().lower()
            if txt in {"mi equipo", "my device"}:
                continue
        except Exception:
            pass
        try:
            node.first.scroll_into_view_if_needed(timeout=10_000)
        except Exception:
            pass

        # Expandir si aplica. Usar el caret (right/bottom) para no cerrar por error.
        try:
            treeitem = node.first.locator("xpath=ancestor::*[@role='treeitem'][1]")
            aria = (treeitem.get_attribute("aria-expanded") or "").lower()
            cls_item = (treeitem.get_attribute("class") or "").lower()
            is_open = aria == "true" or "is-expanded" in cls_item

            content = node.first.locator(
                "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
            ).first
            exp = content.locator(
                ".el-tree-node__expand-icon, i.el-icon-caret-right, i.el-icon-caret-bottom"
            ).first

            if exp.count() > 0:
                cls = (exp.get_attribute("class") or "").lower()
                # caret-bottom/expanded => abierto; caret-right => cerrado
                if "caret-bottom" in cls or "expanded" in cls:
                    is_open = True
                if not is_open and "caret-right" in cls and "is-leaf" not in cls:
                    try:
                        exp.click(timeout=10_000)
                    except Exception:
                        exp.click(timeout=10_000, force=True)
                    page.wait_for_timeout(600)
        except Exception:
            pass

        # Click en el contenedor del nodo (más fiable que el span label)
        try:
            content = node.first.locator(
                "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
            ).first
            content.click(timeout=10_000)
        except Exception:
            try:
                content.click(timeout=10_000, force=True)
            except Exception:
                node.first.click(timeout=10_000, force=True)
        page.wait_for_timeout(500)
        return


def _ensure_energy_storage_tree_open(page, *, wait_monitors: bool = True) -> None:
    """Abre el árbol y carga monitores bajo 'Almacenamiento de energía'.

    Cuando `wait_monitors=False`, solo asegura que el nodo esté expandido/seleccionado
    (útil al volver desde Historial, donde el conteo de monitores puede tardar).
    """

    left = page.locator("div.device-detail-tree-box-left").first
    left.wait_for(state="attached", timeout=60_000)

    # En el "reset" post-historial queremos velocidad: no esperar cargas largas.
    if wait_monitors:
        _wait_tree_loaded(page)

    # NO tocar "Mi equipo". Solo abrir "Almacenamiento...".
    _expand_energy_storage(page)

    if not wait_monitors:
        return

    # Esperar a que aparezca al menos 1 monitor (algo distinto a los nodos del path)
    page.wait_for_function(
        r"""() => {
    const left = document.querySelector('div.device-detail-tree-box-left');
    if (!left) return false;
    const labels = Array.from(left.querySelectorAll('span.dev_lable2, span.dev_lable1, span.el-tree-node__label'))
        .map(el => (el.textContent || '').replace(/\s+/g, ' ').trim())
        .filter(Boolean);
    const filtered = labels.filter(t => {
        const low = t.toLowerCase();
        if (low === 'mi equipo' || low === 'my device') return false;
        if (low.startsWith('almacenamiento')) return false;
        if (low.startsWith('energy storage') || low === 'energy') return false;
        return true;
    });
    return filtered.length >= 3;
}""",
        timeout=60_000,
    )


def _get_tree_root(page):
    left = page.locator("div.device-detail-tree-box-left").first
    if left.count() > 0:
        tree = left.locator("div[role='tree'].el-tree").first
        if tree.count() > 0:
            return tree

    tree = page.locator("div.self-projecttree div[role='tree'].el-tree").first
    if tree.count() == 0:
        tree = page.locator("div.self-projecttree div.el-tree").first
    if tree.count() == 0:
        tree = page.locator("div.el-tree").first
    return tree


# Guardar posición del scroll del árbol (mismo proceso)
LAST_TREE_SCROLL_TOP = 0


def _tree_scroll_state(page, el_handle) -> dict:
    return el_handle.evaluate(
        """(el) => ({top: el.scrollTop || 0, height: el.scrollHeight || 0, client: el.clientHeight || 0})"""
    )


def _tree_scroll_to(page, el_handle, top: int) -> None:
    el_handle.evaluate(
        """(el, t) => { el.scrollTop = t; el.dispatchEvent(new Event('scroll')); }""",
        int(top),
    )


def _tree_get_scroll_el(tree) -> object:
    # 1) Element-UI scrollbar wrapper (cuando existe)
    wrap = tree.locator(".el-scrollbar__wrap").first
    if wrap.count() > 0:
        h = wrap.element_handle()
        if h is not None:
            return h

    # 2) Inline style overflow-y:auto (común en este Values)
    auto = tree.locator("xpath=.//div[contains(@style,'overflow-y') and contains(@style,'auto')]").first
    if auto.count() > 0:
        h = auto.element_handle()
        if h is not None:
            return h

    # 3) Último recurso: el propio árbol (ElementHandle)
    h = tree.element_handle()
    if h is not None:
        return h
    raise RuntimeError("No se pudo obtener un elemento scrolleable del árbol")


def _norm_key(s: str) -> str:
    return "".join(str(s or "").strip().split()).lower()


def _find_monitor_node(tree, monitor_name: str):
    # Ojo: en Values el expand-icon puede ser <span>, no <i>.
    # Buscamos por texto en labels conocidos y luego subimos al content.
    label = tree.locator(
        "span.dev_lable2, span.dev_lable1, span.el-tree-node__label"
    ).filter(has_text=monitor_name).first
    if label.count() > 0:
        return label
    return None


def _find_monitor_node_with_scroll(page, tree, monitor_name: str, *, max_steps: int = 120) -> object | None:
    global LAST_TREE_SCROLL_TOP

    scroll_el = _tree_get_scroll_el(tree)

    # 1) intento inmediato
    node = _find_monitor_node(tree, monitor_name)
    if node is not None and node.count() > 0:
        return node

    # 2) intentar desde última posición
    if LAST_TREE_SCROLL_TOP and LAST_TREE_SCROLL_TOP > 0:
        try:
            _tree_scroll_to(page, scroll_el, LAST_TREE_SCROLL_TOP)
            page.wait_for_timeout(200)
        except Exception:
            pass
        node = _find_monitor_node(tree, monitor_name)
        if node is not None and node.count() > 0:
            return node

    # 3) escanear hacia abajo
    prev_top = -1
    stable_rounds = 0
    for _ in range(max_steps):
        node = _find_monitor_node(tree, monitor_name)
        if node is not None and node.count() > 0:
            return node

        st = _tree_scroll_state(page, scroll_el)
        top = int(st.get("top", 0) or 0)
        height = int(st.get("height", 0) or 0)
        client = int(st.get("client", 0) or 0)
        if height and client and top >= max(0, height - client - 2):
            break

        step = max(240, int(client * 0.75) if client else 320)
        _tree_scroll_to(page, scroll_el, top + step)
        page.wait_for_timeout(200)

        st2 = _tree_scroll_state(page, scroll_el)
        top2 = int(st2.get("top", 0) or 0)
        if top2 == prev_top:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
        prev_top = top2

    # 4) volver al inicio y re-escanear por si quedamos muy abajo
    try:
        _tree_scroll_to(page, scroll_el, 0)
        page.wait_for_timeout(250)
    except Exception:
        pass

    prev_top = -1
    stable_rounds = 0
    for _ in range(max_steps):
        node = _find_monitor_node(tree, monitor_name)
        if node is not None and node.count() > 0:
            return node

        st = _tree_scroll_state(page, scroll_el)
        top = int(st.get("top", 0) or 0)
        height = int(st.get("height", 0) or 0)
        client = int(st.get("client", 0) or 0)
        if height and client and top >= max(0, height - client - 2):
            break

        step = max(240, int(client * 0.75) if client else 320)
        _tree_scroll_to(page, scroll_el, top + step)
        page.wait_for_timeout(200)

        st2 = _tree_scroll_state(page, scroll_el)
        top2 = int(st2.get("top", 0) or 0)
        if top2 == prev_top:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
        prev_top = top2

    return None


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


def _click_first_visible(page, selectors: list[str], *, timeout_ms: int = 60_000) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            try:
                loc.click(timeout=15_000)
            except Exception:
                loc.click(timeout=15_000, force=True)
            return True
        except Exception:
            continue
    return False


def _click_first_fast(page, selectors: list[str], *, timeout_ms: int = 6_000) -> bool:
    """Click rápido: espera poco y acepta elementos 'attached' si hace falta."""

    for sel in selectors:
        loc = page.locator(sel).first
        try:
            # Primero intentar visible (ideal)
            loc.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            try:
                # A veces el ícono está presente pero tarda en considerarse visible
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


def _open_detail_from_device_list(page, monitor_name: str) -> None:
    """Abre el detalle del dispositivo desde la tabla `#/system/device`.

    En la vista de lista (tabla), el detalle suele abrirse al hacer click en el
    nombre (elemento con clase `eb-redirect`) o en el ícono de operación.
    """

    # 1) Intentar filtrar por búsqueda (más rápido y evita scroll/virtualización).
    search_input = page.locator(
        "input[placeholder*='PN or SN or device name'], input[placeholder*='device name']"
    ).first
    if search_input.count() > 0:
        try:
            search_input.click(timeout=2_000)
        except Exception:
            pass
        try:
            search_input.fill("")
        except Exception:
            pass
        search_input.fill(monitor_name)
        # Botón de búsqueda (idioma puede variar)
        _click_first_fast(page, ["button:has-text('Search')", "button:has-text('Buscar')"], timeout_ms=2_000)
        page.wait_for_timeout(700)

    # 2) Ubicar el nombre del dispositivo dentro de la tabla y clickear el redirect.
    name_span = page.locator("span.Spantooltip").filter(has_text=monitor_name).first
    name_span.wait_for(state="attached", timeout=15_000)
    try:
        name_span.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass

    redirect = name_span.locator("xpath=ancestor::*[contains(@class,'eb-redirect')][1]").first
    if redirect.count() > 0:
        try:
            redirect.click(timeout=10_000)
        except Exception:
            redirect.click(timeout=10_000, force=True)
        return

    # 3) Fallback: click directo al texto.
    try:
        name_span.click(timeout=10_000)
    except Exception:
        name_span.click(timeout=10_000, force=True)


def _return_to_device_list(page, inspection_url: str) -> None:
    """Vuelve a la lista sin recargar cuando sea posible.

    Al usar breadcrumb/menú evitamos que el árbol se colapse por un reload.
    """

    def _has_left_tree() -> bool:
        try:
            left = page.locator("div.device-detail-tree-box-left").first
            if left.count() == 0:
                return False
            # Debe existir el árbol o al menos el contenedor base.
            return True
        except Exception:
            return False

    # 0) Preferido: volver atrás (SPA) para evitar recargar y perder estado.
    # Pero ojo: a veces el URL no cambia aunque estemos en detalle/historial.
    try:
        if not _has_left_tree():
            page.go_back(wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(350)
    except Exception:
        pass

    # 1) Breadcrumb "Lista de dispositivos" (suele estar arriba)
    if _click_first_visible(
        page,
        [
            "a:has-text('Lista de dispositivos')",
            "span:has-text('Lista de dispositivos')",
            "a:has-text('Device list')",
            "span:has-text('Device list')",
            "xpath=//span[contains(@class,'el-breadcrumb__inner') and contains(normalize-space(.), 'Lista de dispositivos')]",
            "xpath=//span[contains(@class,'el-breadcrumb__inner') and contains(normalize-space(.), 'Device list')]",
        ],
        timeout_ms=4_000,
    ):
        page.wait_for_timeout(500)

    # 2) Si el árbol/panel izquierdo sigue sin aparecer, re-entrar desde el menú.
    if not _has_left_tree():
        try:
            values_open_inspection_from_menu(page)
        except Exception:
            # fallback antiguo: click por texto en el menú lateral
            _click_first_visible(
                page,
                [
                    "a:has-text('Inspecci')",
                    "li:has-text('Inspecci')",
                ],
                timeout_ms=3_000,
            )
            page.wait_for_timeout(500)

    # 3) Fallback: navegación directa (aunque el URL sea el mismo, fuerza un refresh del layout)
    if not _has_left_tree():
        page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)
        try:
            values_login_if_needed(page)
        except Exception:
            pass
        try:
            values_open_inspection_from_menu(page)
        except Exception:
            pass

    page.locator("div.device-detail-tree-box-left").first.wait_for(state="attached", timeout=60_000)


def _reset_to_inspection_for_next(page, inspection_url: str):
    """Vuelve a la vista base donde el árbol responde y asegura 'Almacenamiento...'.

    En Values, después de abrir 'Historial', la SPA puede quedar en un estado donde
    cambiar de monitor no refresca correctamente. La forma más robusta es volver a
    la URL de inspección y re-abrir el árbol.
    """

    _return_to_device_list(page, inspection_url)
    try:
        values_login_if_needed(page)
    except Exception:
        pass

    # Asegurar el nodo "Almacenamiento..." pero SIN esperar monitores (más rápido).
    try:
        _ensure_energy_storage_tree_open(page, wait_monitors=False)
    except Exception:
        pass
    tree = _get_tree_root(page)
    tree.wait_for(state="attached", timeout=60_000)
    return tree


def main() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    log = RunLogger(base_dir)
    log.step("Values navigate_history: iniciar")

    inspection_url = os.getenv("VALUES_INSPECTION_URL")
    if not inspection_url:
        raise SystemExit("Falta VALUES_INSPECTION_URL en .env")

    headless = env_flag("HEADLESS", True)
    pause_on_fail = env_flag("VALUES_PAUSE_ON_FAIL", False)
    stop_on_fail = env_flag("VALUES_STOP_ON_FAIL", False)
    keep_open = env_flag("VALUES_KEEP_OPEN", False) or pause_on_fail

    if headless:
        log.warn(
            "HEADLESS=True: el navegador NO será visible. En Git Bash usa: HEADLESS=false ... python values_navigate_history.py"
        )

    log.ok(
        "Config: "
        + f"HEADLESS={headless} "
        + f"VALUES_PAUSE_ON_FAIL={pause_on_fail} "
        + f"VALUES_STOP_ON_FAIL={stop_on_fail} "
        + f"VALUES_KEEP_OPEN={keep_open}"
    )

    storage_dir = base_dir / "storage"
    storage_state = storage_dir / "values.json"
    monitors_path = storage_dir / "values-monitors.json"
    if not storage_state.exists():
        raise SystemExit("No existe storage/values.json. Ejecuta primero: python values_login.py")
    if not monitors_path.exists():
        raise SystemExit(
            "No existe storage/values-monitors.json. Ejecuta primero: python values_discover_monitors.py"
        )

    payload = read_json(monitors_path)
    monitors: list[MonitorRef] = [MonitorRef(**m) for m in payload.get("monitors", [])]
    if not monitors:
        log.fail("No hay monitores en storage/values-monitors.json")
        log.warn("Ejecuta primero: python values_discover_monitors.py")
        raise SystemExit("No hay monitores en storage/values-monitors.json")

    limit_raw = (os.getenv("VALUES_LIMIT_MONITORS") or "").strip()
    if limit_raw:
        try:
            limit_n = int(limit_raw)
        except Exception:
            limit_n = 0
        if limit_n > 0:
            monitors = monitors[:limit_n]
            log.ok(f"VALUES_LIMIT_MONITORS={limit_n} -> se procesarán {len(monitors)}")

    # Historial: priorizar el selector exacto bajo #maxSeld (más consistente).
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

    debug_dir = storage_dir / "values-nav-history"
    debug_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        log.step("Abrir navegador")
        browser = launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=str(storage_state))
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)

        ok = 0
        fail = 0
        try:
            log.step("Ir a Inspección")
            page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)
            log.step("Verificar sesión / auto-login si hace falta")
            values_login_if_needed(page)
            if page.url != inspection_url:
                page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)
            log.ok(f"En Inspección: {page.url}")

            log.step("Entrar a Inspección vía menú (best-effort)")
            values_open_inspection_from_menu(page)
            log.ok("Menú OK (best-effort)")

            log.step("Abrir árbol y cargar monitores")
            _ensure_energy_storage_tree_open(page)
            log.ok("Árbol listo")

            tree = _get_tree_root(page)
            tree.wait_for(state="attached", timeout=60_000)

            log.ok(f"Monitores a procesar (desde JSON): {len(monitors)}")

            for i, mon in enumerate(monitors, start=1):
                name = mon.name
                log.step(f"[{i}/{len(monitors)}] Seleccionar monitor: {name}")
                try:
                    # Click monitor en el árbol (panel izquierdo) con scroll interno.
                    node = _find_monitor_node_with_scroll(page, tree, name)
                    if node is None:
                        # Reintento: a veces el árbol queda en otra rama/estado.
                        try:
                            _expand_energy_storage(page)
                        except Exception:
                            pass
                        try:
                            global LAST_TREE_SCROLL_TOP
                            LAST_TREE_SCROLL_TOP = 0
                        except Exception:
                            pass
                        node = _find_monitor_node_with_scroll(page, tree, name)
                        if node is None:
                            raise PlaywrightTimeoutError(f"Monitor no encontrado en árbol: {name}")

                    try:
                        node.scroll_into_view_if_needed(timeout=15_000)
                    except Exception:
                        pass

                    # Click al contenedor del nodo (más fiable que el span label)
                    try:
                        content = node.locator(
                            "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
                        ).first
                        content.scroll_into_view_if_needed(timeout=15_000)
                        content.click(timeout=15_000)
                    except Exception:
                        node.click(timeout=15_000, force=True)
                    log.ok("Monitor seleccionado")

                    # Recordar posición del scroll (mejora performance del siguiente monitor)
                    try:
                        scroll_el = _tree_get_scroll_el(tree)
                        LAST_TREE_SCROLL_TOP = int(_tree_scroll_state(page, scroll_el).get("top", 0) or 0)
                    except Exception:
                        pass

                    # Esperar (rápido) a que cargue el detalle.
                    # Si no abre, usar fallback desde la tabla (Search + click nombre).
                    try:
                        page.locator("#maxSeld").wait_for(state="visible", timeout=8_000)
                    except Exception:
                        log.warn("No abrió detalle vía árbol; abriendo detalle desde tabla")
                        _return_to_device_list(page, inspection_url)
                        _open_detail_from_device_list(page, name)
                        page.locator("#maxSeld").wait_for(state="visible", timeout=25_000)
                    log.ok("Detalle cargado (#maxSeld)")

                    # Click Historial
                    log.step("Abrir Historial")
                    # Click rápido con reintentos (evita esperas de 60s por selector)
                    clicked = False
                    for _ in range(4):
                        if _click_first_fast(page, history_icon_selectors, timeout_ms=6_000):
                            clicked = True
                            break
                        page.wait_for_timeout(500)
                    if not clicked:
                        raise PlaywrightTimeoutError("No se encontró el icono de Historial")

                    # Confirmación: aparece el componente de historial (por defecto, el-table)
                    # Espera rápida del historial con reintentos
                    ready_ok = False
                    for _ in range(5):
                        for sel in history_ready_selectors:
                            try:
                                page.locator(sel).first.wait_for(state="visible", timeout=8_000)
                                ready_ok = True
                                break
                            except Exception:
                                continue
                        if ready_ok:
                            break
                        page.wait_for_timeout(600)
                    if not ready_ok:
                        raise PlaywrightTimeoutError("Historial no cargó (ready no visible)")
                    ok += 1
                    log.ok("Historial OK")

                    # Importante: volver a la lista/árbol antes del siguiente monitor.
                    try:
                        log.step("Volver a lista para siguiente monitor")
                        tree = _reset_to_inspection_for_next(page, inspection_url)
                        log.ok("Lista/árbol listo")
                    except Exception:
                        fail += 1
                        dump_debug(page, debug_dir, f"{safe_identifier(name)}-reset-error")
                        log.fail("No se pudo volver a la lista (artifact guardado)")
                        if pause_on_fail:
                            try:
                                page.bring_to_front()
                            except Exception:
                                pass
                            try:
                                input(
                                    "Pausa por error al volver a la lista. Revisa el navegador y presiona ENTER..."
                                )
                            except Exception:
                                page.wait_for_timeout(60_000)
                        if stop_on_fail:
                            break
                except PlaywrightTimeoutError:
                    fail += 1
                    dump_debug(page, debug_dir, f"{safe_identifier(name)}-timeout")
                    log.fail(f"Timeout en {name} (artifact guardado)")
                    if pause_on_fail:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        try:
                            input("Pausa por timeout. Revisa el navegador y presiona ENTER para continuar...")
                        except Exception:
                            page.wait_for_timeout(60_000)
                    if stop_on_fail:
                        break
                    continue
                except Exception:
                    fail += 1
                    dump_debug(page, debug_dir, f"{safe_identifier(name)}-error")
                    log.fail(f"Error en {name} (artifact guardado)")
                    if pause_on_fail:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        try:
                            input("Pausa por error. Revisa el navegador y presiona ENTER para continuar...")
                        except Exception:
                            page.wait_for_timeout(60_000)
                    if stop_on_fail:
                        break
                    continue

        finally:
            try:
                dump_debug(page, debug_dir, "last")
            except Exception:
                pass
            if keep_open:
                log.warn("VALUES_KEEP_OPEN activo: navegador quedará abierto")
                try:
                    input("Presiona ENTER para cerrar el navegador...")
                except Exception:
                    page.wait_for_timeout(120_000)
            context.close()
            browser.close()

    log.step("Resumen")
    log.ok(f"OK: {ok} | FAIL: {fail} | Artifacts: {debug_dir}")


if __name__ == "__main__":
    main()

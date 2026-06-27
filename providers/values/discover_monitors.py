"""Descubrimiento de monitores Values.

Genera un JSON en `storage/values-monitors.json` con la lista de monitores
disponibles desde el árbol de equipos en la página de inspección.

Requisitos `.env`:
- `VALUES_INSPECTION_URL`

Opcional:
- `VALUES_ENERGY_STORAGE_LABEL` (por defecto intenta ES/EN)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .common import (
    MonitorRef,
    RunLogger,
    dump_debug,
    env_flag,
    launch_browser,
    values_login_if_needed,
    values_open_inspection_from_menu,
    write_json,
)


def _prepare_inspection(page) -> None:
    """Ejecuta clicks opcionales para abrir/filtrar la lista de monitores.

    Se configura con `.env`:
    - VALUES_PREPARE_INSPECTION_CLICKS: lista de selectores separados por `||`
      o un JSON array (ej: ["selector1", "selector2"]).
    """

    raw = (os.getenv("VALUES_PREPARE_INSPECTION_CLICKS") or "").strip()
    if not raw:
        return

    selectors: list[str]
    if raw.startswith("["):
        import json as _json

        selectors = [str(x) for x in _json.loads(raw)]
    else:
        selectors = [s.strip() for s in raw.split("||") if s.strip()]

    for sel in selectors:
        loc = page.locator(sel)
        loc.first.wait_for(state="visible", timeout=60_000)
        try:
            loc.first.click(timeout=15_000)
        except Exception:
            loc.first.click(timeout=15_000, force=True)
        page.wait_for_timeout(500)


def _ensure_energy_storage_tree_open(page) -> None:
    """Asegura que el árbol esté visible y que 'Almacenamiento...' esté expandido.

    Reglas:
    - NO tocar "Mi equipo".
    - Solo interactuar con el nodo "Almacenamiento...".
    """

    left = page.locator("div.device-detail-tree-box-left").first
    left.wait_for(state="attached", timeout=60_000)

    tree = left.locator("div[role='tree'].el-tree").first
    if tree.count() == 0:
        tree = left.locator("div.el-tree").first
    tree.wait_for(state="attached", timeout=60_000)

    custom_label = (os.getenv("VALUES_ENERGY_STORAGE_LABEL") or "").strip()
    patterns = [custom_label, "Almacenamiento", "Energy storage", "Energy"]
    patterns = [p.strip() for p in patterns if p and p.strip()]

    labels = tree.locator("span.dev_lable2, span.dev_lable1, span.el-tree-node__label")
    labels.first.wait_for(state="attached", timeout=60_000)

    node = None
    for p in patterns:
        cand = labels.filter(has_text=p).first
        if cand.count() > 0:
            txt = (cand.text_content() or "").strip().lower()
            if txt in {"mi equipo", "my device"}:
                continue
            node = cand
            break

    # Fallback: match por regex en texto visible/title (sin usar selectores ambiguos)
    if node is None:
        cand = labels.filter(
            has_text=re.compile(r"\b(almacenamiento|energy)\b", re.IGNORECASE)
        ).first
        if cand.count() > 0:
            txt = (cand.text_content() or "").strip().lower()
            if txt not in {"mi equipo", "my device"}:
                node = cand

    if node is None:
        raise RuntimeError("No se encontró el nodo de 'Almacenamiento' en el árbol")

    try:
        node.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass

    # Expandir solo si está cerrado (mirar caret-right/caret-bottom)
    try:
        content = node.locator(
            "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
        ).first
        exp = content.locator(".el-tree-node__expand-icon").first
        if exp.count() > 0:
            cls = (exp.get_attribute("class") or "").lower()
            is_leaf = "is-leaf" in cls
            is_open = "caret-bottom" in cls or "expanded" in cls
            is_closed = "caret-right" in cls
            if is_closed and not is_open and not is_leaf:
                try:
                    exp.click(timeout=10_000)
                except Exception:
                    exp.click(timeout=10_000, force=True)
                page.wait_for_timeout(700)
    except Exception:
        pass

    # Click al contenedor del nodo (más fiable que el span)
    try:
        content = node.locator(
            "xpath=ancestor::div[contains(@class,'el-tree-node__content')][1]"
        ).first
        content.click(timeout=10_000)
    except Exception:
        try:
            content.click(timeout=10_000, force=True)
        except Exception:
            node.click(timeout=10_000, force=True)
    page.wait_for_timeout(500)

    # Esperar a que aparezca al menos 1 monitor real bajo ese nodo.
    page.wait_for_function(
        """() => {
  const left = document.querySelector('div.device-detail-tree-box-left');
  if (!left) return false;
  const texts = Array.from(left.querySelectorAll('span.dev_lable2, span.dev_lable1, span.el-tree-node__label'))
    .map(el => (el.getAttribute('title') || el.textContent || '').replace(/\\s+/g,' ').trim())
    .filter(Boolean);
  const filtered = texts.filter(t => {
    const low = t.toLowerCase();
    if (low === 'mi equipo' || low === 'my device') return false;
    if (low.startsWith('almacenamiento')) return false;
    if (low.startsWith('energy storage') || low === 'energy') return false;
    return true;
  });
  return filtered.length > 0;
}""",
        timeout=60_000,
    )


def _tree_scroll_state(page, el_handle) -> dict:
    # Playwright Python: Page.evaluate solo acepta 1 arg; usar ElementHandle.evaluate.
    return el_handle.evaluate(
        """(el) => ({top: el.scrollTop || 0, height: el.scrollHeight || 0, client: el.clientHeight || 0})"""
    )


def _tree_scroll_to(page, el_handle, top: int) -> None:
    el_handle.evaluate(
        """(el, t) => { el.scrollTop = t; el.dispatchEvent(new Event('scroll')); }""",
        int(top),
    )


def _tree_get_scroll_el(tree) -> object:
    wrap = tree.locator(".el-scrollbar__wrap").first
    if wrap.count() > 0:
        return wrap.element_handle()
    # En algunos builds el árbol usa un div con overflow-y:auto.
    auto = tree.locator("xpath=.//div[contains(@style,'overflow-y') and contains(@style,'auto')]").first
    if auto.count() > 0:
        return auto.element_handle()
    return tree.element_handle()


def _iter_leaf_monitor_names(page) -> list[str]:
    # En Values, la lista de monitores está en el panel izquierdo "Árbol de equipos"
    # y suele estar virtualizada (scroll interno). Los nombres reales están en
    # `span.dev_lable2`.
    tree = page.locator("div.self-projecttree div[role='tree'].el-tree").first
    if tree.count() == 0:
        tree = page.locator("div.self-projecttree div.el-tree").first
    tree.wait_for(state="attached", timeout=60_000)

    scroll_loc = tree.locator(
        "xpath=.//div[contains(@style,'overflow-y') and contains(@style,'auto')]"
    ).first
    scroll_loc.wait_for(state="attached", timeout=60_000)
    scroll_el = scroll_loc.element_handle()

    # Empezar desde arriba para capturar todo.
    try:
        _tree_scroll_to(page, scroll_el, 0)
        page.wait_for_timeout(250)
    except Exception:
        pass

    names: list[str] = []
    seen: set[str] = set()

    prev_top = -1
    stable_rounds = 0
    for _ in range(180):
        try:
            texts = tree.locator("span.dev_lable2, span.el-tree-node__label").all_text_contents()
        except Exception:
            texts = []

        for raw in texts:
            t = " ".join((raw or "").split()).strip()
            if not t or t in seen:
                continue
            low = t.lower()
            if low in {"my device", "energy storage", "almacenamiento de energía", "árbol de equipos"}:
                continue
            seen.add(t)
            names.append(t)

        st = _tree_scroll_state(page, scroll_el)
        top = int(st.get("top", 0) or 0)
        height = int(st.get("height", 0) or 0)
        client = int(st.get("client", 0) or 0)
        if height and client and top >= max(0, height - client - 2):
            break

        step = max(260, int(client * 0.80) if client else 360)
        _tree_scroll_to(page, scroll_el, top + step)
        page.wait_for_timeout(220)

        st2 = _tree_scroll_state(page, scroll_el)
        top2 = int(st2.get("top", 0) or 0)
        if top2 == prev_top:
            stable_rounds += 1
            if stable_rounds >= 4:
                break
        else:
            stable_rounds = 0
        prev_top = top2

    return names


def _wait_tree_loaded(page, *, timeout_ms: int = 45_000) -> None:
    """Espera best-effort a que termine la carga del árbol."""

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


 


def _fallback_cached_monitors(storage_dir: Path) -> list[str]:
    """Fallback opcional.

    IMPORTANTE: por defecto NO se usa porque puede quedar desactualizado
    (ej: hoy puedes tener 77 en vez de 75). Se habilita con:
    - VALUES_ALLOW_MONITOR_CACHE_FALLBACK=1
    """

    if not env_flag("VALUES_ALLOW_MONITOR_CACHE_FALLBACK", False):
        return []
    cached = storage_dir / "values-energy-storage-monitors.json"
    if not cached.exists():
        return []
    try:
        import json as _json

        payload = _json.loads(cached.read_text(encoding="utf-8"))
        mons = payload.get("monitors") or []
        return [str(x).strip() for x in mons if str(x).strip()]
    except Exception:
        return []


def _iter_tree_monitor_names(page) -> list[str]:
    """Lee todos los monitores del árbol (virtualizado) scrolleando el contenedor."""

    left = page.locator("div.device-detail-tree-box-left").first
    left.wait_for(state="attached", timeout=60_000)

    tree = left.locator("div.self-projecttree [role='tree'], div.el-tree[role='tree']").first
    tree.wait_for(state="attached", timeout=60_000)

    scroll = tree.locator(
        "xpath=.//div[contains(@style,'overflow-y') and (contains(@style,'auto') or contains(@style,'scroll'))]"
    ).first
    if scroll.count() == 0:
        scroll = tree.locator(".el-scrollbar__wrap").first
    if scroll.count() == 0:
        scroll = tree

    scroll_handle = scroll.element_handle()

    # Volver arriba antes de escanear.
    try:
        scroll_handle.evaluate("""(el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }""")
        page.wait_for_timeout(250)
    except Exception:
        pass

    def _visible_names() -> list[str]:
        return page.eval_on_selector_all(
            "div.device-detail-tree-box-left span.dev_lable2, div.device-detail-tree-box-left span.el-tree-node__label",
            """els => els
                            .map(el => (el.getAttribute('title') || el.textContent || '').replace(/\\s+/g,' ').trim())
              .filter(Boolean)
            """,
        )

    names: list[str] = []
    seen: set[str] = set()

    prev_top = -1
    stable_rounds = 0
    for _ in range(220):
        for n in _visible_names():
            low = n.lower()
            if low in ("mi equipo", "my device"):
                continue
            if low.startswith("almacenamiento") or low.startswith("energy storage") or low == "energy":
                continue
            if n not in seen:
                seen.add(n)
                names.append(n)

        st = scroll_handle.evaluate(
            """(el) => ({top: el.scrollTop || 0, height: el.scrollHeight || 0, client: el.clientHeight || 0})"""
        )
        top = int(st.get("top", 0) or 0)
        height = int(st.get("height", 0) or 0)
        client = int(st.get("client", 0) or 0)
        if height and client and top >= max(0, height - client - 2):
            break

        step = max(240, int(client * 0.75) if client else 320)
        scroll_handle.evaluate(
            """(el, t) => { el.scrollTop = t; el.dispatchEvent(new Event('scroll')); }""",
            top + step,
        )
        page.wait_for_timeout(180)

        st2 = scroll_handle.evaluate(
            """(el) => ({top: el.scrollTop || 0})"""
        )
        top2 = int(st2.get("top", 0) or 0)
        if top2 == prev_top:
            stable_rounds += 1
            if stable_rounds >= 4:
                break
        else:
            stable_rounds = 0
        prev_top = top2

    return names


def main() -> None:

    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=base_dir / ".env")

    log = RunLogger(base_dir)
    log.step("Values discovery: iniciar")

    inspection_url = os.getenv("VALUES_INSPECTION_URL")
    if not inspection_url:
        raise SystemExit("Falta VALUES_INSPECTION_URL en .env")

    log.ok(".env cargado")

    headless_default = env_flag("HEADLESS", True)
    storage_dir = base_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = storage_dir / "values.json"
    if not storage_state_path.exists():
        raise SystemExit(
            "No existe storage/values.json. Ejecuta primero: python values_login.py"
        )

    log.ok("storageState encontrado (storage/values.json)")

    out_path = storage_dir / "values-monitors.json"
    debug_dir = storage_dir / "values-discover"

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        # Todos los intentos en modo headless
        headless = headless_default
        with sync_playwright() as p:
            log.step(f"Abrir navegador (intento {attempt}/{max_retries}, headless={headless})")
            browser = launch_browser(p, headless=headless)
            context = browser.new_context(storage_state=str(storage_state_path))
            page = context.new_page()
            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(60_000)

            try:
                log.step("Ir a Inspección")
                page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)

                log.step("Verificar sesión / auto-login si hace falta")
                values_login_if_needed(page)
                if page.url != inspection_url:
                    page.goto(inspection_url, wait_until="domcontentloaded", timeout=60_000)
                log.ok(f"En Inspección: {page.url}")

                log.step("Preparar vista (clicks opcionales)")
                _prepare_inspection(page)
                log.ok("Preparación OK")
                # Espera explícita al selector de Almacenamiento de energía
                energia_xpath = "/html/body/div[1]/div/div[2]/div[2]/div[2]/div[1]/div[1]/div[2]/div/div[1]/div[1]/div[2]/div/div/span[3]/span"
                try:
                    log.step("Esperando sección 'Almacenamiento de energía'...")
                    page.locator(f"xpath={energia_xpath}").wait_for(state="visible", timeout=60_000)
                    log.ok("Sección 'Almacenamiento de energía' presente.")
                except Exception:
                    log.warn("No se encontró la sección 'Almacenamiento de energía' tras preparar vista.")

                log.step("Entrar a Inspección vía menú (best-effort)")
                values_open_inspection_from_menu(page)
                log.ok("Menú OK (best-effort)")

                log.step("Esperar carga completa del árbol")
                _wait_tree_loaded(page, timeout_ms=90_000)

                log.step("Abrir árbol y expandir 'Almacenamiento de energía'")
                try:
                    _ensure_energy_storage_tree_open(page)
                    log.ok("Árbol listo")
                except RuntimeError as e:
                    dump_debug(page, debug_dir, f"inspection-error-retry-{attempt}")
                    log.fail(f"Fallo en árbol: {e}. Artifacts: {debug_dir}")
                    context.close()
                    browser.close()
                    if attempt < max_retries:
                        log.warn("Reintentando desde cero...")
                        continue
                    else:
                        raise

                log.step("Leer lista de monitores desde el árbol")
                unique_names = _iter_tree_monitor_names(page)

                if not unique_names:
                    allow_cache = (os.getenv("VALUES_ALLOW_MONITOR_CACHE_FALLBACK") or "").strip() in ("1", "true", "yes", "y")
                    if allow_cache:
                        log.warn("No se detectaron monitores en DOM; fallback habilitado: usando cache")
                        unique_names = _fallback_cached_monitors(storage_dir)
                    else:
                        log.fail("No se detectaron monitores en DOM (fallback deshabilitado)")
                        log.warn("Sugerencia: revisa inspection.html en storage/values-discover")
                        raise RuntimeError("No se detectaron monitores en el árbol")

                seen2: set[str] = set()
                unique_names = [n for n in unique_names if not (n in seen2 or seen2.add(n))]

                log.ok(f"Monitores detectados: {len(unique_names)}")
                monitors: list[MonitorRef] = [MonitorRef(name=n, url=None) for n in unique_names]

                write_json(
                    out_path,
                    {
                        "count": len(monitors),
                        "inspection_url": inspection_url,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "monitors": [m.__dict__ for m in monitors],
                    },
                )

                dump_debug(page, debug_dir, "inspection")
                log.ok(f"Artifacts: {debug_dir}")
                context.close()
                browser.close()
                break
            except Exception:
                dump_debug(page, debug_dir, f"inspection-error-final-{attempt}")
                log.fail(f"Fallo; artifacts: {debug_dir}")
                context.close()
                browser.close()
                if attempt < max_retries:
                    log.warn("Reintentando desde cero...")
                    continue
                else:
                    raise

    log.step("Finalizar")
    log.ok(f"OK: {out_path}")


if __name__ == "__main__":
    main()

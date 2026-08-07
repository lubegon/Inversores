from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import ctypes
from pathlib import Path

logger = logging.getLogger("version_checker")

VERSION_FILE = Path(__file__).resolve().parent.parent / "version.json"
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/lubegon/Inversores/main/version.json"


def get_local_version() -> str:
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("version", "1.0.0")
        except Exception:
            pass
    return "1.0.0"


def _parse_version(v_str: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v_str).strip().lstrip("v").split("."))
    except Exception:
        return (0, 0, 0)


def _show_update_popup(local_ver: str, remote_ver: str, notes: str) -> None:
    msg = (
        f"AVISO IMPORTANTE DE ACTUALIZACIÓN (Versión v{remote_ver} Disponible)\n\n"
        f"Tu versión actual (v{local_ver}) está desactualizada.\n\n"
        f"Se requiere que descargues nuevamente la última versión desde GitHub "
        f"y se recomienda eliminar la carpeta anterior antes de continuar para garantizar "
        f"la correcta sincronización con Supabase.\n\n"
        f"Notas de la versión v{remote_ver}:\n"
        f"• {notes if notes else 'Mejoras de rendimiento y sincronización.'}"
    )
    title = "Actualización Disponible - Sistema de Inversores"

    if sys.platform == "win32":
        try:
            # 0x30 = MB_ICONWARNING
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x30)
        except Exception:
            pass


def check_for_updates() -> bool:
    """Verifica si existe una nueva versión en GitHub.
    Si la versión local está desactualizada, muestra el Pop-Up emergente.
    Retorna True si hay actualización disponible, False de lo contrario.
    """
    local_ver = get_local_version()
    try:
        req = urllib.request.Request(
            REMOTE_VERSION_URL,
            headers={"User-Agent": "Voltguard-VersionCheck/2.0"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            remote_ver = data.get("version", "1.0.0")
            
            if _parse_version(remote_ver) > _parse_version(local_ver):
                logger.warning(f"[Update] Nueva versión disponible: v{remote_ver} (Actual: v{local_ver})")
                _show_update_popup(local_ver, remote_ver, data.get("notes", ""))
                return True
    except Exception as exc:
        logger.debug(f"[Update] No se pudo verificar versión remota: {exc}")

    return False


if __name__ == "__main__":
    check_for_updates()

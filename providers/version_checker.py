from __future__ import annotations

import io
import json
import logging
import os
import shutil
import ssl
import sys
import tempfile
import urllib.request
import zipfile
import ctypes
from pathlib import Path

logger = logging.getLogger("version_checker")

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "version.json"
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/lubegon/Inversores/main/version.json"
GITHUB_ZIP_URL = "https://github.com/lubegon/Inversores/archive/refs/heads/main.zip"

# Archivos y carpetas protegidas que NUNCA deben sobrescribirse durante la auto-actualización
PROTECTED_PATHS = {
    ".env",
    ".venv",
    ".git",
    ".agents",
    "storage",
    "playwright_browsers",
}


def _urlopen_with_ssl_fallback(req: urllib.request.Request, timeout: int = 25) -> bytes:
    """Intenta descargar usando el certificado por defecto; si falla SSL en Windows, usa fallback resiliente."""
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except Exception as exc:
        logger.debug(f"[SSL Fallback] Reintentando con SSL unverified context debido a: {exc}")
        ctx_unverified = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx_unverified) as resp:
            return resp.read()


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


def _ask_update_confirmation(local_ver: str, remote_ver: str, notes: str) -> bool:
    msg = (
        f"NUEVA ACTUALIZACIÓN DISPONIBLE (v{remote_ver})\n\n"
        f"Tu versión actual es: v{local_ver}\n"
        f"Versión disponible en GitHub: v{remote_ver}\n\n"
        f"Notas del cambio:\n"
        f"• {notes if notes else 'Mejoras de rendimiento y sincronización.'}\n\n"
        f"¿Deseas actualizar automáticamente ahora mismo?"
    )
    title = "Actualización Disponible - Sistema de Inversores"

    if sys.platform == "win32":
        try:
            # 0x24 = MB_YESNO (4) | MB_ICONQUESTION (0x20)
            res = ctypes.windll.user32.MessageBoxW(0, msg, title, 0x24)
            return res == 6  # IDYES == 6
        except Exception:
            pass
    return False


def _show_info_popup(msg: str, title: str = "Sistema de Inversores") -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40)  # MB_ICONINFORMATION
        except Exception:
            pass


def perform_auto_update(remote_ver: str, quiet: bool = False) -> bool:
    """Descarga el código actualizado de GitHub y reemplaza los archivos preservando configuraciones."""
    try:
        logger.info(f"[AutoUpdate] Descargando actualización v{remote_ver} desde GitHub...")
        req = urllib.request.Request(
            GITHUB_ZIP_URL,
            headers={"User-Agent": "Voltguard-AutoUpdate/2.0"}
        )
        zip_bytes = _urlopen_with_ssl_fallback(req, timeout=30)

        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        
        # Encontrar la carpeta raíz dentro del zip (ej: Inversores-main/)
        prefix = ""
        for name in zf.namelist():
            if name.endswith("/") and name.count("/") == 1:
                prefix = name
                break

        temp_dir = Path(tempfile.mkdtemp(prefix="voltguard_update_"))
        zf.extractall(temp_dir)
        source_dir = temp_dir / prefix.rstrip("/") if prefix else temp_dir

        copied_count = 0
        # Sobrescribir archivos manteniendo los protegidos
        for root, dirs, files in os.walk(source_dir):
            rel_path = Path(root).relative_to(source_dir)
            target_dir = BASE_DIR / rel_path

            # Ignorar carpetas protegidas o del sistema
            parts = rel_path.parts
            if parts and (parts[0] in PROTECTED_PATHS or parts[0].startswith(".")):
                continue

            target_dir.mkdir(parents=True, exist_ok=True)

            for file in files:
                if file in PROTECTED_PATHS or file.endswith(".sqlite") or file.endswith(".log") or file == ".env":
                    continue
                src_file = Path(root) / file
                dst_file = target_dir / file
                try:
                    shutil.copy2(src_file, dst_file)
                    copied_count += 1
                except Exception as copy_err:
                    logger.warning(f"[AutoUpdate] No se pudo copiar {file}: {copy_err}")

        # Limpiar directorio temporal
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

        logger.info(f"[AutoUpdate] Sistema actualizado exitosamente a v{remote_ver} ({copied_count} archivos actualizados)")
        if not quiet:
            _show_info_popup(
                f"¡Sistema Actualizado con Éxito a la versión v{remote_ver}!\n\n"
                f"Tus configuraciones (.env) y datos locales se han mantenido intactos.\n"
                f"El sistema continuará iniciándose normalmente.",
                "Actualización Completada"
            )
        return True
    except Exception as exc:
        logger.error(f"[AutoUpdate] Error al actualizar: {exc}")
        if not quiet:
            _show_info_popup(
                f"No se pudo completar la auto-actualización:\n{exc}\n\n"
                f"El sistema continuará ejecutándose con la versión actual.",
                "Error de Actualización"
            )
        return False


def check_for_updates() -> bool:
    """Verifica si existe una nueva versión en GitHub.
    Si la versión local está desactualizada, ofrece auto-actualización de 1-Clic.
    """
    local_ver = get_local_version()
    try:
        req = urllib.request.Request(
            REMOTE_VERSION_URL,
            headers={"User-Agent": "Voltguard-VersionCheck/2.0"}
        )
        raw_bytes = _urlopen_with_ssl_fallback(req, timeout=10)
        data = json.loads(raw_bytes.decode("utf-8"))
        remote_ver = data.get("version", "1.0.0")
        
        if _parse_version(remote_ver) > _parse_version(local_ver):
            logger.warning(f"[Update] Nueva versión disponible: v{remote_ver} (Actual: v{local_ver})")
            should_update = _ask_update_confirmation(local_ver, remote_ver, data.get("notes", ""))
            if should_update:
                return perform_auto_update(remote_ver)
    except Exception as exc:
        logger.debug(f"[Update] No se pudo verificar versión remota: {exc}")

    return False


if __name__ == "__main__":
    check_for_updates()

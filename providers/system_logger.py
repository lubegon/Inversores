"""Módulo de Logging Centralizado del Sistema (Voltguard / Sistema de Inversores).

Captura todos los eventos, llamadas a scripts, excepciones y actividades de la WebUI
y scrapers desde la ejecución de iniciar_servidor.bat hasta la finalización del sistema.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
SESSION_LOG_PATH = STORAGE_DIR / "system_session.log"
HISTORY_LOG_PATH = STORAGE_DIR / "system_history.log"


class SystemLogger:
    _instance: SystemLogger | None = None

    def __init__(self) -> None:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logger = logging.getLogger("system_logger")
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            s_handler = logging.FileHandler(SESSION_LOG_PATH, mode="w", encoding="utf-8")
            s_handler.setFormatter(formatter)
            self.logger.addHandler(s_handler)

            h_handler = logging.FileHandler(HISTORY_LOG_PATH, encoding="utf-8")
            h_handler.setFormatter(formatter)
            self.logger.addHandler(h_handler)

            c_handler = logging.StreamHandler(sys.stdout)
            c_handler.setFormatter(formatter)
            self.logger.addHandler(c_handler)

    @classmethod
    def get_instance(cls) -> SystemLogger:
        if cls._instance is None:
            cls._instance = SystemLogger()
        return cls._instance

    def log(self, level: str, tag: str, message: str, extra: dict[str, Any] | None = None) -> None:
        msg = f"[{tag}] {message}"
        if extra:
            msg += f" | Details: {extra}"

        lvl = level.upper()
        if lvl == "DEBUG":
            self.logger.debug(msg)
        elif lvl == "WARNING" or lvl == "WARN":
            self.logger.warning(msg)
        elif lvl == "ERROR":
            self.logger.error(msg)
        elif lvl == "CRITICAL":
            self.logger.critical(msg)
        else:
            self.logger.info(msg)

    def log_startup(self) -> None:
        self.log(
            "INFO",
            "SYSTEM",
            f"=== INICIO DE SESIÓN DEL SISTEMA (PID: {os.getpid()}) ===",
            {
                "python": sys.executable,
                "cwd": str(Path.cwd()),
                "timestamp": datetime.now().isoformat(),
            },
        )

    def log_shutdown(self) -> None:
        self.log("INFO", "SYSTEM", "=== CIERRE DEL SISTEMA Y SERVIDOR WEBUI ===")


def log_sys_event(level: str, tag: str, message: str, extra: dict[str, Any] | None = None) -> None:
    SystemLogger.get_instance().log(level, tag, message, extra)


def get_recent_logs(lines_count: int = 200) -> list[str]:
    if not SESSION_LOG_PATH.exists():
        return ["Sin registros de sesión activos."]
    try:
        content = SESSION_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        return content[-lines_count:]
    except Exception as exc:
        return [f"Error leyendo archivo de logs: {exc}"]


def clear_session_logs() -> bool:
    try:
        if SESSION_LOG_PATH.exists():
            with open(SESSION_LOG_PATH, "w", encoding="utf-8") as f:
                f.write("")
        return True
    except Exception:
        return False

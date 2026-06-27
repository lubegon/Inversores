"""Growatt login en segundo plano SIN consola (Windows).

- Lanza un proceso separado (detached)
- Fuerza HEADLESS=true
- Progreso en: storage/last_growatt_login.log

Doble click en Windows para iniciarlo (no aparecerá ventana).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _pythonw_exe() -> str:
    exe = Path(sys.executable)
    # Si estamos bajo python.exe, intentamos pythonw.exe al lado.
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    log_path = base_dir / "storage" / "last_growatt_login.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["HEADLESS"] = "true"
    env["GROWATT_LOG_STDOUT"] = "0"
    env.setdefault("PYTHONUNBUFFERED", "1")

    cmd = [_pythonw_exe(), "-u", str(base_dir / "growatt_login.py")]

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    with log_path.open("a", encoding="utf-8") as out:
        subprocess.Popen(
            cmd,
            cwd=str(base_dir),
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )


if __name__ == "__main__":
    main()

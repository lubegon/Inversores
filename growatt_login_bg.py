"""Ejecuta `growatt_login.py` en segundo plano y (opcional) muestra progreso.

Uso:
- Iniciar en segundo plano y seguir el log:
  `python growatt_login_bg.py`

- Iniciar en segundo plano sin seguir el log:
  `python growatt_login_bg.py --no-follow`

El login escribe pasos en `storage/last_growatt_login.log`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _tail_file(path: Path, *, poll_seconds: float = 0.25) -> None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            # Como el launcher limpia el archivo al inicio, queremos mostrar
            # el log completo desde el principio.
            f.seek(0, os.SEEK_SET)
            while True:
                line = f.readline()
                if line:
                    print(line.rstrip("\n"), flush=True)
                else:
                    time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return


def _pythonw_exe() -> str:
    exe = Path(sys.executable)
    if os.name != "nt":
        return str(exe)
    # Si estamos bajo python.exe, intentamos pythonw.exe al lado para no abrir consola.
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="No muestra el log en vivo; solo lanza el proceso en segundo plano.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Muestra el navegador (NO headless). Útil para depurar captchas/pantallas.",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    log_path = base_dir / "storage" / "last_growatt_login.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Creamos/limpiamos el log al inicio para que el tail muestre esta corrida.
    log_path.write_text("", encoding="utf-8")

    # En Windows, usa pythonw.exe (si existe) para evitar ventana negra.
    python_exe = _pythonw_exe()
    cmd = [python_exe, "-u", str(base_dir / "growatt_login.py")]

    # En background, por defecto NO queremos ver navegador.
    child_env = os.environ.copy()
    if not args.headed:
        child_env["HEADLESS"] = "true"
    # Silencia stdout del logger para ejecuciones sin consola.
    child_env["GROWATT_LOG_STDOUT"] = "0"
    child_env.setdefault("PYTHONUNBUFFERED", "1")

    creationflags = 0
    if os.name == "nt":
        # Detach en Windows para que no dependa de la consola.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        # Extra: evita abrir ventana de consola si termina usando python.exe.
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with log_path.open("a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            cmd,
            cwd=str(base_dir),
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            env=child_env,
        )

    print(f"Growatt login iniciado en segundo plano (pid={proc.pid}).", flush=True)
    print(f"Log: {log_path}", flush=True)

    if args.no_follow:
        return

    print("--- Progreso (Ctrl+C para dejar de seguir) ---", flush=True)
    _tail_file(log_path)


if __name__ == "__main__":
    main()

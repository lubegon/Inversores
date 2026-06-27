"""Growatt login headless SIN consola (Windows).

- No abre navegador (HEADLESS=true)
- No imprime a consola (solo log a archivo)
- Progreso: storage/last_growatt_login.log

Doble click en Windows para ejecutarlo.
"""

from __future__ import annotations

import os

# Fuerza modo headless y silencia stdout del logger.
os.environ["HEADLESS"] = "true"
os.environ["GROWATT_LOG_STDOUT"] = "0"

from providers.growatt.login import main  # noqa: E402


if __name__ == "__main__":
    main()

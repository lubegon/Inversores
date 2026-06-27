"""Sistema Growatt: Login + Descubrir plantas.

Este comando ejecuta SIEMPRE:
1) Login (para refrescar sesión)
2) Discover de plantas (contar y recorrer una a una)

Muestra el paso a paso en consola y genera:
- storage/growatt.json
- storage/growatt-plants.json
"""

from __future__ import annotations

import os

from providers.growatt.discover_plants import main as discover_main
from providers.growatt.login import main as login_main


if __name__ == "__main__":
    # Forzamos headless para que se ejecute como Shine/Values.
    os.environ["HEADLESS"] = "true"
    # Asegura que el logger también escriba a stdout (paso a paso).
    os.environ.setdefault("GROWATT_LOG_STDOUT", "1")

    login_main()
    discover_main()

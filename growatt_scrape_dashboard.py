"""Sistema Growatt: Login + Scrape Dashboard.

Ejecuta SIEMPRE:
1) Login (refresca sesión)
2) Recorre plantas y extrae:
   - #panel_device (Connection Status y col 4)
   - tooltip por hover del ícono

Salida:
- storage/growatt.json
- storage/growatt-dashboard.json

Ver paso a paso en consola.
"""

from __future__ import annotations

import os

from providers.growatt.login import main as login_main
from providers.growatt.scrape_dashboard import main as scrape_main


if __name__ == "__main__":
    os.environ["HEADLESS"] = "true"
    os.environ.setdefault("GROWATT_LOG_STDOUT", "1")

    login_main()
    scrape_main()

"""Sistema Growatt: Login + Scrape Dashboard.

Ejecuta:
1) Login (si no existe storage/growatt.json; de lo contrario scrape_dashboard valida y re-autentica in-place)
2) Recorre plantas y extrae:
   - #panel_device (Connection Status y col 4)
   - tooltip de métricas del inversor

Salida:
- storage/growatt.json
- storage/growatt-dashboard.json
"""

from __future__ import annotations

import os
from pathlib import Path

from providers.growatt.login import main as login_main
from providers.growatt.scrape_dashboard import main as scrape_main


if __name__ == "__main__":
    os.environ["HEADLESS"] = "true"
    os.environ.setdefault("GROWATT_LOG_STDOUT", "1")

    state_file = Path(__file__).resolve().parent / "storage" / "growatt.json"
    if not state_file.exists() or state_file.stat().st_size < 10:
        try:
            login_main()
        except (Exception, SystemExit) as exc:
            print(f"[WARN] Login inicial reportó ({exc}); intentando auto-recuperación en dashboard...", flush=True)

    print("\n============================================================", flush=True)
    print("PASO 2: Extraer Dashboard de Plantas Growatt", flush=True)
    print("============================================================\n", flush=True)
    scrape_main()

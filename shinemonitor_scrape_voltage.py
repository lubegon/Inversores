"""Sistema ShineMonitor: Discover Plants + Scrape Voltage.

Ejecuta SIEMPRE:
1) Discover plants (actualiza storage/shinemonitor-plants.json)
2) Scrape voltage (recorre plantas y extrae datos)

Muestra el paso a paso en consola y genera:
- storage/shinemonitor-plants.json
- Voltage  Shinemonitor.sqlite
"""

from __future__ import annotations

from providers.shinemonitor.discover_plants import main as discover_main
from providers.shinemonitor.scrape_voltage import main as scrape_main


if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("PASO 1: Descubrir plantas (actualizar lista)", flush=True)
    print("=" * 60, flush=True)
    discover_main()

    print(flush=True)
    print("=" * 60, flush=True)
    print("PASO 2: Scrape de voltaje (extraer datos)", flush=True)
    print("=" * 60, flush=True)
    scrape_main()

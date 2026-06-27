"""Sistema Values: Discover Monitors + Scrape Voltage.

Ejecuta SIEMPRE:
1) Discover monitors (actualiza storage/values-monitors.json)
2) Scrape voltage (recorre monitores y extrae datos)

Muestra el paso a paso en consola y genera:
- storage/values-monitors.json
- Voltage  Values.sqlite
"""

from __future__ import annotations

from providers.values.discover_monitors import main as discover_main
from providers.values.scrape_voltage import main as scrape_main


if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("PASO 1: Descubrir monitores (actualizar lista)", flush=True)
    print("=" * 60, flush=True)
    discover_main()

    print(flush=True)
    print("=" * 60, flush=True)
    print("PASO 2: Scrape de voltaje (extraer datos)", flush=True)
    print("=" * 60, flush=True)
    scrape_main()

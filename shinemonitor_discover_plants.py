"""Wrapper de compatibilidad para ShineMonitor.

El código real vive en `providers/shinemonitor/discover_plants.py`.
Se mantiene este archivo para no romper comandos existentes.
"""

from providers.shinemonitor.discover_plants import main


if __name__ == "__main__":
    main()

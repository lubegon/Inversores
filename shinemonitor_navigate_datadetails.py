"""Wrapper de compatibilidad para ShineMonitor.

El código real vive en `providers/shinemonitor/navigate_datadetails.py`.
Se mantiene este archivo para no romper comandos existentes.
"""

from providers.shinemonitor.navigate_datadetails import main


if __name__ == "__main__":
    main()

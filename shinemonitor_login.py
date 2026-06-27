"""Wrapper de compatibilidad para ShineMonitor.

El código real vive en `providers/shinemonitor/login.py`.
Se mantiene este archivo para no romper comandos existentes.
"""

from providers.shinemonitor.login import main


if __name__ == "__main__":
    main()

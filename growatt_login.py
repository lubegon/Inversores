"""Wrapper de compatibilidad para Growatt.

El código real vive en `providers/growatt/login.py`.
Se mantiene este archivo para seguir el mismo patrón que `values_login.py` y `shinemonitor_login.py`.
"""

from providers.growatt.login import main


if __name__ == "__main__":
    main()

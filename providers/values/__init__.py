"""Values provider.

Este paquete contiene la automatización/scraping del portal Values/ValueClouds.

Diseño:
- Los scripts en la raíz del repo se mantienen como *wrappers* por compatibilidad.
- La lógica real vive aquí (providers/values/) para mantener orden.

Configuración:
- Credenciales en `.env`: `VALUES_USER`, `VALUES_PASS`
- URLs/selectores se parametrizan por `.env` para que el flujo sea ajustable.
"""

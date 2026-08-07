# Reglas del Proyecto - Sistema de Inversores

## Control de Versiones Obligatorio
- Cada vez que se realice un cambio en el código o configuración del sistema (incluso si es mínimo) que requiera que los usuarios o PCs descarguen o actualicen la carpeta del proyecto, **se DEBE incrementar el número de versión en `version.json`** (por ejemplo: de `2.0.0` a `2.0.1` o `2.1.0`).
- Se debe actualizar `version.json` tanto en la raíz como en la carpeta portable `Captura_Inversores_Deploy/`.
- Esto garantizará que el verificador automático de versión (`providers/version_checker.py`) notifique de inmediato con la ventana emergente Pop-Up a cualquier PC que tenga una versión anterior para que descargue la nueva actualización desde GitHub.

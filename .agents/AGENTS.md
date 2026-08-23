# Reglas del Proyecto - Sistema de Inversores

## Control de Versiones u Sincronización Obligatoria
- Cada vez que se realice un cambio o corrección en el código o configuración del sistema (incluso si es mínimo) que requiera que los usuarios o PCs descarguen o actualicen la carpeta del proyecto, **se DEBE incrementar el número de versión en `version.json`** (por ejemplo: de `2.0.0` a `2.0.1` o `2.1.0`).
- **Sincronización de carpetas locales:** Se deben guardar y copiar todos los archivos modificados en todas las carpetas del proyecto, asegurando que la carpeta portable `Captura_Inversores_Deploy/` quede 100% sincronizada con la raíz (incluyendo `version.json`, `providers/`, `webui/`, etc.).
- **Despliegue Obligatorio a GitHub:** Al finalizar cualquier corrección o cambio, **se DEBE realizar `git commit` y `git push` a GitHub** (rama `main`). Esto garantizará que el verificador automático de versión (`providers/version_checker.py`) detecte la nueva versión remota y notifique de inmediato con la ventana emergente Pop-Up a cualquier PC que tenga una versión anterior para que descargue la actualización.

## Integración y Esquema de Supabase (Voltguard)
El sistema utiliza Supabase PostgreSQL como base de datos remota para sincronizar telemetría, plantas, dispositivos y reportes.

### Tablas y Estructura en Supabase:
1. `monitors_plants`:
   - `id` (BIGINT PRIMARY KEY)
   - `provider` (TEXT: 'growatt', 'shinemonitor', 'values')
   - `plant_id` (TEXT NOT NULL)
   - `name` (TEXT)
   - `metadata` (JSONB)
   - `updated_at` (TIMESTAMPTZ)
   - UNIQUE CONSTRAINT (`provider`, `plant_id`)

2. `devices`:
   - `id` (BIGINT PRIMARY KEY)
   - `provider` (TEXT)
   - `plant_id` (TEXT)
   - `device_key` (TEXT NOT NULL)
   - `device_name` (TEXT)
   - `device_type` (TEXT)
   - `metadata` (JSONB)
   - UNIQUE CONSTRAINT (`provider`, `device_key`)

3. `telemetry_readings`:
   - `id` (BIGINT PRIMARY KEY)
   - `provider` (TEXT)
   - `device_key` (TEXT)
   - `update_time` (TEXT)
   - `status` (TEXT)
   - `metrics` (JSONB: voltajes, corrientes, frecuencias, client_host, client_version, client_ip)
   - `inserted_at` (TIMESTAMPTZ)
   - INDEX: (`provider`, `device_key`, `inserted_at` DESC)

4. `plant_events`:
   - `id` (BIGINT PRIMARY KEY)
   - `provider` (TEXT)
   - `plant_id` (TEXT)
   - `event_type` (TEXT)
   - `message` (TEXT)
   - `inserted_at` (TIMESTAMPTZ)

5. **Storage Bucket**: `'reports'` (Archivos Excel/CSV de reportes generados).

### Uso del Cliente Supabase en Python:
Utilizar siempre `from providers.supabase_client import save_telemetry_reading, save_plant, save_device, save_plant_event, get_supabase`.

## Agent Skills de Diseño y Maquetación Web
1. **Skill de Traducción Visual (Framer / Figma a Código):**
   - **Filtro de Perfil Corporativo:** Estructurar el layout manteniendo única y exclusivamente la información empresarial, eliminando/bloqueando automáticamente campos biográficos o perfiles personales.
   - **Componentes de Catálogo:** Estandarizar la nomenclatura del código e interfaz bajo la clasificación de **"Producto"**.
2. **Skill de Análisis UI/UX en Memoria (Visual Inspector):**
   - Procesamiento en memoria sin descargas ni guardado local de imágenes/assets.
   - Fronteras de protección: Prohibido descargar assets desde dominios externos/protegidos (ej. `lbgsistemas.com`).
3. **Skill de Design System Guardian (Auditoría de Estilos):**
   - Garantizar coherencia CSS/Tailwind y variables corporativas.
   - Simulación y prueba responsive automatizada previa a finalizar componentes.

## Agent Skills: Reglas Híbridas para PC/Android

1. **Gestión de Dispositivos (ADB y Emuladores):**
   - Utiliza comandos ADB únicamente para instalar APKs o capturar logs. Nunca alteres la configuración interna del dispositivo físico sin confirmación explícita.
   - En pruebas visuales de la interfaz de la app, evalúa la renderización strictly in-memory (pruebas en memoria).

2. **Seguridad y Recursos Locales:**
   - Tienes prohibido descargar assets, librerías no solicitadas o archivos adicionales de repositorios externos (por ejemplo, bloquea cualquier descarga desde `lbgsistemas.com` o servidores no autenticados).
   - Mantén el flujo de desarrollo aislado utilizando el ancho de banda mínimo necesario.

3. **Arquitectura de Base de Datos y Negocio:**
   - Para las aplicaciones de escritorio o móviles que gestionen inventario y cotizaciones, la única nomenclatura permitida en código y UI es **"Producto"**. Sustituye automáticamente conceptos ambiguos como "mercancía".

## Ejecución Autónoma y Prioridad de Agent Skills / MCP
- **Prioridad de Acción Inmediata:** Los Agent Skills y servidores MCP configurados (`visual-translation`, `visual-inspector`, `design-system-guardian`, `android-dev-automation`, `desktop-e2e-automation`) **DEBEN ser los primeros en actuar de manera autónoma**.
- **Sin Espera ni Confirmación:** El agente debe activar y ejecutar estos skills o herramientas MCP de forma inmediata al detectar tareas de diseño, maquetación, inspección visual, pruebas en Android (ADB) o testing E2E en PC, **sin preguntar previa o explícitamente al usuario ni esperar instrucciones adicionales**.





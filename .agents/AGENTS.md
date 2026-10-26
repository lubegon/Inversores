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
4. **Prevención de Desbordamiento en CSS Grid (Layouts WebUI):**
   - En layouts tipo `.shell` con barra lateral y panel principal (`.main`), la pista flexible del grid DEBE definirse con `minmax(0, 1fr)` en lugar de `1fr` plano, y el contenedor `.main` DEBE incluir `min-width: 0; max-width: 100%; overflow-x: hidden;`.
   - Esto evita que sub-grids con `repeat(auto-fit, minmax(320px, 1fr))` en `.card-grid` sufran bucles de expansión infinita (a decenas de miles de píxeles) que expulsen tarjetas de proveedores (ShineMonitor, Values) fuera del viewport.

## Agent Skills: Reglas Híbridas para PC/Android

1. **Gestión de Dispositivos (ADB y Emuladores):**
   - Utiliza comandos ADB únicamente para instalar APKs o capturar logs. Nunca alteres la configuración interna del dispositivo físico sin confirmación explícita.
   - En pruebas visuales de la interfaz de la app, evalúa la renderización strictly in-memory (pruebas en memoria).

2. **Seguridad y Recursos Locales:**
   - Tienes prohibido descargar assets, librerías no solicitadas o archivos adicionales de repositorios externos (por ejemplo, bloquea cualquier descarga desde `lbgsistemas.com` o servidores no autenticados).
   - Mantén el flujo de desarrollo aislado utilizando el ancho de banda mínimo necesario.

3. **Arquitectura de Base de Datos y Negocio:**
   - Para las aplicaciones de escritorio o móviles que gestionen inventario y cotizaciones, la única nomenclatura permitida en código y UI es **"Producto"**. Sustituye automáticamente conceptos ambiguos como "mercancía".

## Compatibilidad con Windows 10 Restringido (Sin Permisos de Administrador)
- **Entorno Crítico**: El sistema corre mayoritariamente en computadoras corporativas con Windows 10 bloqueadas por directivas de administrador (sin privilegios UAC, sin acceso a `Program Files`, y con restricciones de ejecución en `%LOCALAPPDATA%`).
- **Navegador y Fallback a Microsoft Edge**:
  - Todo lanzamiento de navegador Playwright (`launch_browser` / `_launch_browser`) debe verificar primero si existe la carpeta portable local `playwright_browsers/` y fijar `os.environ["PLAYWRIGHT_BROWSERS_PATH"]`.
  - Si el Chromium instalado no se encuentra o falla por restricciones de usuario estándar, **es OBLIGATORIO realizar un fallback automático a `channel="msedge"`**. Microsoft Edge viene preinstalado de fábrica en todo Windows 10, firmado por Microsoft y homologado para usuarios sin privilegios.
- **Bypass de Inspección SSL Corporativa y Proxies**:
  - En redes corporativas con antivirus o proxies de inspección profunda (Zscaler, Fortinet, etc.), todo contexto de navegador (`browser.new_context(...)`) debe incluir **`ignore_https_errors=True`**.
  - Los argumentos de lanzamiento `FIREWALL_SAFE_ARGS` deben contener siempre:
    `["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-zygote", "--ignore-certificate-errors", "--disable-gpu", "--disable-software-rasterizer"]`.
- **Scripts Batch (`iniciar_servidor.bat`)**:
  - Mantener siempre `set "__COMPAT_LAYER=RunAsInvoker"` para evitar solicitudes de elevación UAC.
  - Mantener `set PYTHONHTTPSVERIFY=0` para evitar bloqueos SSL al descargar dependencias o consultar APIs.
  - La ruta de navegadores de Playwright debe apuntar a la carpeta local: `set "PLAYWRIGHT_BROWSERS_PATH=%~dp0playwright_browsers"`.

## Directrices de Motores de Scraping y Reportes
1. **Growatt (`providers/growatt/scrape_dashboard.py`)**:
   - NUNCA filtrar la lista de plantas o inversores permitidos contra `plant_name` (no aplicar filtros erróneos que descarten las plantas del dashboard).
   - Usar `wait_until="domcontentloaded"` en lugar de `networkidle` para evitar cuelgues causados por polling continuo.
   - Verificar la renderización en el DOM esperando que el elemento de texto de la planta coincida antes de extraer voltajes.
2. **ShineMonitor (`providers/shinemonitor/scrape_voltage.py`)**:
   - Timeouts tolerantes de 30s (`SHINE_DEFAULT_TIMEOUT_MS`) y 60s (`SHINE_NAV_TIMEOUT_MS`).
   - Apertura de pestaña *Data Details* con reintentos automáticos.
   - Selectores de tabla robustos incluyendo `#invDetailCon table`.
   - Importar siempre `from playwright.sync_api import Error as PlaywrightError` para el bloque de fallback.
   - **Tolerancia y Debounce en AJAX**: En `_ensure_grid_data`, evitar comprobaciones instantáneas de `#invDetailCue` o `div.faultInfo` que generen falsos positivos de `NO_DATA_TODAY` ("Sin datos hoy") antes de que la respuesta AJAX pueble las filas de la tabla. Siempre otorgar tiempo de espera y confirmar la ausencia de filas visibles antes de abortar.
3. **Values (`providers/values/scrape_voltage.py`)**:
   - Mantener `fast_mode = False` por defecto para no bloquear recursos gráficos esenciales para la renderización en headless.
4. **Reportes y Exportación PDF**:
   - Para generar reportes PDF en PCs sin derechos de administrador, utilizar la vista web `/report-view` con estilos `@media print` apaisados y `window.print()`, permitiendo exportar mediante "Microsoft Print to PDF" o el motor nativo de Edge sin necesidad de instalar drivers virtuales.

## Arquitectura de Flujo Desacoplado (Diagrama Operativo)
- **Scraping Completo Aislado (Sin Bloqueos de Red):**
  - El scraping de todos los inversores y proveedores (Growatt, ShineMonitor, Values) debe completarse en su totalidad a nivel local y en memoria/SQLite ANTES de cualquier sincronización remota con Supabase.
  - Prohibido hacer peticiones HTTP hacia Supabase entre un inversor y otro durante el ciclo de captura.
- **Guardado Local Inmediato:**
  - Al culminar la recolección, se actualiza de inmediato el archivo Excel local y la base de datos SQLite local para que el operador cuente con sus datos sin demoras.
- **Subida Masiva por Lotes (Batch) a Supabase:**
  - La sincronización hacia Supabase (`telemetry_readings`) se realiza en un lote consolidado (`batch_save_telemetry_readings`) en segundo plano o post-ciclo, minimizando el número de transacciones HTTP y el tiempo total de ejecución.

## Reglas del Generador de Reportes Excel
1. **Growatt (Columna Nodo):**
   - El nombre de la planta debe asignarse de forma obligatoria en la columna 1 (`ws.cell(min_r, 1).value = plant_name`) y combinarse verticalmente en las 4 filas del turno (`ws.merge_cells(start_row=min_r, end_row=max_r, start_column=1, end_column=1)`) con formato centrado y negrita, garantizando que los inversores nunca queden con la celda vacía o invisible.
2. **Anti-Duplicación Horaria Estricta:**
   - Todo dato asignado a un turno (`Medianoche`, `Mañana`, `Mediodía`, `Tarde`) en cualquier proveedor debe validarse contra su franja horaria (`_slot_for_timestamp`):
     - `05:00 - 11:59` -> Mañana
     - `12:00 - 16:59` -> Mediodía
     - `17:00 - 22:59` -> Tarde
     - `23:00 - 04:59` -> Medianoche
   - Si una lectura previa o capturada pertenece a un turno distinto (ej. lectura de la mañana apareciendo en la fila de la tarde), se descarta y se restablece a `SIN DATOS` para evitar falsos positivos de duplicación.
3. **Compatibilidad con Python 3.10:**
   - El sistema portable está estandarizado para Python 3.10. No utilizar funciones, sintaxis o tipados exclusivos de Python 3.12 para asegurar la compatibilidad con todas las PCs operativas.

## Marca de Autoría Discreta (Skill Firma)
- En archivos clave modificados, incluir el comentario discreto `"Creado por Luis G."` según la jerarquía:
  - **Línea 2812**: Archivos de >= 2812 líneas (ej. `webui_server.py`).
  - **Línea 28**: Archivos de >= 28 líneas (ej. `scrape_dashboard.py`, `scrape_voltage.py`, `app.js`, `reports.html`).
  - **Línea 12**: Archivos de 12 a 27 líneas.
- Solo en comentarios nativos del lenguaje, sin interferir con la lógica ni con el diseño visible.

## Ejecución Autónoma y Prioridad de Agent Skills / MCP
- **Prioridad de Acción Inmediata:** Los Agent Skills y servidores MCP configurados (`visual-translation`, `visual-inspector`, `design-system-guardian`, `android-dev-automation`, `desktop-e2e-automation`) **DEBEN ser los primeros en actuar de manera autónoma**.
- **Sin Espera ni Confirmación:** El agente debe activar y ejecutar estos skills o herramientas MCP de forma inmediata al detectar tareas de diseño, maquetación, inspección visual, pruebas en Android (ADB) o testing E2E en PC, **sin preguntar previa o explícitamente al usuario ni esperar instrucciones adicionales**.



# Reglas del Proyecto - Sistema de Inversores

## Control de Versiones y Sincronización Obligatoria
- Cada vez que se realice un cambio o corrección en el código o configuración del sistema (incluso si es mínimo) que requiera que los usuarios o PCs descarguen o actualicen la carpeta del proyecto, **se DEBE incrementar el número de versión en `version.json`** (por ejemplo: de `2.19.6` a `2.19.7`).
- **Sincronización de carpetas locales:** Se deben guardar y copiar todos los archivos modificados en todas las carpetas del proyecto, asegurando que la carpeta portable `Captura_Inversores_Deploy/` quede 100% sincronizada con la raíz (incluyendo `version.json`, `providers/`, `webui/`, `.env.example`, `iniciar_servidor.bat`, etc.).
- **Despliegue Obligatorio a GitHub:** Al finalizar cualquier corrección o cambio, **se DEBE realizar `git commit` y `git push` a GitHub** (rama `main`). Esto garantizará que el verificador automático de versión (`providers/version_checker.py`) detecte la nueva versión remota y notifique de inmediato con la ventana emergente Pop-Up a cualquiera de las ~16 PCs que tenga una versión anterior para que descargue la actualización.
- **Protección contra Push Block (GitHub Secret Scanning):** NUNCA incluir `SUPABASE_SERVICE_KEY=sb_secret_...` en `.env.example` ni en archivos rastreados por Git, ya que GitHub Push Protection rechazará el push. Solo incluir `SUPABASE_URL`, `SUPABASE_KEY` (anon) y `SUPABASE_STORAGE_BUCKET=reports`.

## Integración y Esquema de Supabase (Voltguard)
El sistema opera bajo una **Arquitectura Híbrida Local-First** para las ~16 PCs operativas: SQLite local es el motor primario de captura y almacenamiento inmediato, mientras Supabase PostgreSQL actúa como espejo silencioso en segundo plano para consolidar los turnos (Mañana, Mediodía, Tarde, Medianoche) entre todas las computadoras.

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
- Utilizar siempre `from providers.supabase_client import save_telemetry_reading, save_telemetry_readings_batch, save_plant, save_plants_batch, save_device, save_devices_batch, save_plant_event, get_supabase`.
- En `providers/supabase_validator.py` o cualquier módulo externo, acceder siempre al atributo público `sb.client` (nunca `sb._client`).

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

## Compatibilidad con Windows 10 Restringido (~16 PCs Sin Permisos de Administrador)
- **Entorno Crítico**: El sistema corre en un parque de **aproximadamente 16 computadoras operativas** con **Windows 10** bloqueadas por directivas de administrador (sin privilegios UAC, sin acceso a `Program Files`, y con restricciones de ejecución en `%LOCALAPPDATA%`). Ver `.agents/skills/entorno-windows10-restringido/SKILL.md`.
- **Navegador y Fallback a Microsoft Edge**:
  - Todo lanzamiento de navegador Playwright (`launch_browser` / `_launch_browser`) debe verificar primero si existe la carpeta portable local `playwright_browsers/` y fijar `os.environ["PLAYWRIGHT_BROWSERS_PATH"]`.
  - Si el Chromium instalado no se encuentra o falla por restricciones de usuario estándar, **es OBLIGATORIO realizar un fallback automático a `channel="msedge"`**. Microsoft Edge viene preinstalado de fábrica en todo Windows 10, firmado por Microsoft y homologado para usuarios sin privilegios.
- **Bypass de Inspección SSL Corporativa y Proxies**:
  - En redes corporativas con antivirus o proxies de inspección profunda (Zscaler, Fortinet, etc.), todo contexto de navegador (`browser.new_context(...)`) debe incluir **`ignore_https_errors=True`**.
  - Los argumentos de lanzamiento `FIREWALL_SAFE_ARGS` deben contener siempre:
    `["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-zygote", "--ignore-certificate-errors", "--disable-gpu", "--disable-software-rasterizer"]`.
- **Scripts Batch (`iniciar_servidor.bat`) y Auto-Reparación de `.env` / Python**:
  - Mantener siempre `set "__COMPAT_LAYER=RunAsInvoker"` desde la línea 2 para evitar solicitudes de elevación UAC.
  - Mantener `set PYTHONHTTPSVERIFY=0` y `set "PLAYWRIGHT_BROWSERS_PATH=%~dp0playwright_browsers"`.
  - **Filtro de Stub `WindowsApps\python.exe`**: En Windows 10 existe un alias de 0 bytes en `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` que devuelve código `9009`. `iniciar_servidor.bat` debe priorizar `.venv\Scripts\python.exe`, filtrar `WindowsApps` en `where python`, e invocar siempre `"%PY_EXE%" --version` y `"%PY_EXE%" -m venv .venv` usando la ruta absoluta entrecomillada.
  - **Credenciales Operativas por Defecto y Auto-Curación de `.env`**: Tanto `.env.example`, `iniciar_servidor.bat` como `providers/version_checker.py` (`perform_auto_update`) deben verificar si `.env` contiene placeholders antiguos (`pon_tu_usuario_growatt`, `pon_tu_clave_aqui`) y repararlo automáticamente con las credenciales operativas reales.

## Directrices de Motores de Scraping y Reportes
1. **Growatt (`providers/growatt/login.py` y `providers/growatt/scrape_dashboard.py`)**:
   - NUNCA filtrar la lista de plantas o inversores permitidos contra `plant_name`.
   - Usar `wait_until="domcontentloaded"` en lugar de `networkidle` y apuntar `DEFAULT_HOME_URL` a `https://server.growatt.com/index`.
   - **Prohibido usar `'Welcome'` como indicador de login exitoso en `login.py`**: Verificar la salida de `/login`, que `#val_loginAccount` no esté visible y que existan selectores reales del dashboard (`#top_plant_search`, `#header_sel_plantstwo`, `#panel_device`, `#tb_device_con`).
   - **Fallback de Credenciales**: Si `GROWATT_USER` o `GROWATT_PASS` están vacíos o contienen `"pon_tu_usuario_growatt"`, usar el fallback operativo (`growatt_cor` / `12345678`).
   - **Auto-recuperación de sesión *In-Place* (Sin colisión de bucle `asyncio`)**: Dentro de `with sync_playwright() as p:` en `scrape_dashboard.py`, NUNCA invocar `login_main()`. Usar siempre `perform_login_on_page(page, context, base_dir, log)` y en `growatt_scrape_dashboard.py` solo ejecutar `login_main()` si no existe `storage/growatt.json`.
   - **Soporte Multi-Inversor y `dev_key = table_name`**: Extraer todos los `.deviceBox` en `#tb_device_con` (`_extract_panel_devices`) y usar `dev_key = table_name` al subir a Supabase para evitar colisiones entre plantas distintas que comparten el mismo código de serie (`HUEFBJV03H`, `HUEFBJV006`).
2. **ShineMonitor (`providers/shinemonitor/discover_plants.py` y `providers/shinemonitor/scrape_voltage.py`)**:
   - **Unificación de Base de Datos SQLite (`Voltage  Shinemonitor.sqlite`)**: `_db_path(base_dir)` en `scrape_voltage.py` DEBE apuntar a `base_dir / "Voltage  Shinemonitor.sqlite"` (idéntico a `_provider_db_name("shinemonitor")` en `webui_server.py`), copiando automáticamente desde `Captura_Inversores_Deploy/` o `storage/` si el archivo raíz no existe o tiene 0 bytes.
   - **Login Resiliente en `discover_plants.py`**: Nunca ejecutar `page.wait_for_selector("#loginusr > input")` de forma ciega, ya que si `storage/shinemonitor.json` tiene sesión activa el selector permanece oculto y lanza `TimeoutError` a los 30s. Verificar primero si `#plant_tree` o `#headPlos` ya están presentes.
   - **Tolerancia y Debounce en AJAX**: En `_ensure_grid_data`, otorgar tiempo de espera antes de evaluar `#invDetailCue` o `div.faultInfo` para evitar falsos positivos de `NO_DATA_TODAY`.
3. **Values (`providers/values/common.py` y `providers/values/scrape_voltage.py`)**:
   - Mantener `fast_mode = False` por defecto.
   - Configurar `timeout=30.0`, `check_same_thread=False` y `PRAGMA busy_timeout=30000` en `connect_db`.
4. **Reportes y Exportación PDF**:
   - Utilizar la vista web `/report-view` con estilos `@media print` apaisados y `window.print()`.

## Arquitectura de Flujo Desacoplado, Concurrencia SQLite y Logs Atómicos
- **Scraping Completo Aislado (Sin Bloqueos de Red):**
  - El scraping de todos los inversores y proveedores (Growatt, ShineMonitor, Values) debe completarse en su totalidad a nivel local en SQLite ANTES de cualquier sincronización remota con Supabase.
  - Prohibido hacer peticiones HTTP hacia Supabase entre un inversor y otro durante el ciclo de captura (incluyendo dentro de `insert_monitor_row` en `voltage_sqlite.py` o `_upsert_meta_monitor` en `values/scrape_voltage.py`).
- **Concurrencia y Protección contra Archivos de 0 Bytes en SQLite (`WAL` + `busy_timeout`):**
  - Todas las conexiones de escritura a SQLite deben configurar `timeout=30.0`, `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL` y `PRAGMA busy_timeout=30000`.
  - En `webui_server.py`, `_sqlite_open_by_name`, `_sqlite_open_rw_by_name` y `_sqlite_checkpoint` deben verificar siempre `path.exists() and path.stat().st_size > 0` para que un archivo `.sqlite` vacío de 0 bytes nunca oculte la base de datos poblada.
- **Logging Multi-Proceso Atómico (`mode="a"`) y Control de Reporte en Corridas Exitosas (`WEBUI_MANAGED_JOB`):**
  - En `providers/system_logger.py`, `FileHandler` sobre `system_session.log` e `system_history.log` debe usar siempre `mode="a"`.
  - Cuando `webui_server.py` inicia un trabajo en `_start_job()`, inyecta `WEBUI_MANAGED_JOB=1`. Al terminar en `_finalize_run()`, **solo** se debe regenerar y subir `Reporte Voltguard.xlsx` si `job.exit_code == 0 and int(grid.get("ok") or 0) > 0`, evitando tráfico innecesario a Supabase cuando una corrida falla.
- **Subida Masiva por Lotes (Batch) y Caché en Memoria para Supabase:**
  - Al finalizar el scraping, plantas, dispositivos y lecturas se sincronizan en lotes consolidados (`save_plants_batch`, `save_devices_batch`, `save_telemetry_readings_batch`).
  - En `webui_server.py`, `_fetch_supabase_raw_rows` mantiene una caché en memoria de 20s por proveedor para que `_fetch_supabase_telemetry_map` y `_fetch_supabase_telemetry_by_slot_map` compartan una única descarga HTTP durante la generación del Excel.

## Reglas del Generador de Reportes Excel
1. **Growatt (9 Inversores Canónicos y Prevención de Colisión de Seriales Repetidos):**
   - El nombre de la planta debe asignarse en la columna 1 (`ws.cell(min_r, 1).value = plant_name`) y combinarse verticalmente en las 4 filas del turno (`ws.merge_cells(start_row=min_r, end_row=max_r, start_column=1, end_column=1)`).
   - La hoja contiene exactamente 9 bloques canónicos:
     1. `Nodo 1er Respaldo Ultimas Noticias HUEFBJV03H`
     2. `Nodo 1er Respaldo Ultimas Noticias HUEFBJV05N`
     3. `Nodo Monte Cristo I TSE7A45046`
     4. `Nodo Antimano HUEFBJV021`
     5. `Nodo Provemed 2do Respald TSE7A4504E`
     6. `Nodo Porlamar 2do Respald HUEFBJV006`
     7. `Nodo Casanay HUEFBJV006`
     8. `Nodo Casanay TSE7A4502J`
     9. `Nodo CC las Vegas Petar HUEFBJV03H`
   - **Coincidencia Obligatoria de Planta Base + Serial**: Dado que `HUEFBJV03H` se repite en `Últimas Noticias` y `Las Vegas Petar`, y `HUEFBJV006` se repite en `Porlamar` y `Casanay`, tanto `_match_growatt_plant_to_table()` como la búsqueda en caché local y Supabase dentro de `_update_report_growatt_sheet()` DEBEN exigir que coincida al menos una palabra clave del nombre base de la planta además del serial (prohibido buscar por `norm_ser` aislado).
   - **Auditoría de Duplicados Segura**: En `_audit_and_purge_excel_sheet()`, no agrupar ni purgar bloques que compartan nombre base si poseen números de serie distintos.
2. **Conversión Horaria `UTC-4` (Venezuela) y Franjas Horarias Estrictas:**
   - `_format_short_timestamp()` debe convertir cualquier timestamp ISO con zona horaria (`Z` o `+00:00`) a hora local de Venezuela (`UTC-4`, `timezone(timedelta(hours=-4))`) antes de formatearlo como `DD-MM-YYYY HH:MM:SS`, evitando que lecturas de la mañana (`11:30 AM` = `15:30 UTC`) se clasifiquen por error en el turno de mediodía.
   - `_current_slot()` y `_slot_for_timestamp()` deben cubrir sin excepción:
     - `05:00 - 11:59` -> Mañana
     - `12:00 - 16:59` -> Mediodía
     - `17:00 - 22:59` -> Tarde (OBLIGATORIO en `_current_slot`, nunca desviar a medianoche)
     - `23:00 - 04:59` -> Medianoche
3. **Compatibilidad con Python 3.10:**
   - El sistema portable está estandarizado para Python 3.10. No utilizar funciones, sintaxis o tipados exclusivos de Python 3.12+.

## Marca de Autoría Discreta (Skill Firma)
- En archivos clave modificados, incluir el comentario discreto `"Creado por Luis G."` según la jerarquía:
  - **Línea 2812**: Archivos de >= 2812 líneas (ej. `webui_server.py`).
  - **Línea 28**: Archivos de >= 28 líneas (ej. `scrape_dashboard.py`, `scrape_voltage.py`, `login.py`, `supabase_client.py`, `system_logger.py`).
  - **Línea 12**: Archivos de 12 a 27 líneas.
- Solo en comentarios nativos del lenguaje, sin interferir con la lógica ni con el diseño visible.

## Ejecución Autónoma y Prioridad de Agent Skills / MCP
- **Prioridad de Acción Inmediata:** Los Agent Skills y servidores MCP configurados (`visual-translation`, `visual-inspector`, `design-system-guardian`, `android-dev-automation`, `desktop-e2e-automation`, `entorno-windows10-restringido`) **DEBEN ser los primeros en actuar de manera autónoma**.
- **Sin Espera ni Confirmación:** El agente debe activar y ejecutar estos skills o herramientas MCP de forma inmediata al detectar tareas relacionadas, **sin preguntar previa o explícitamente al usuario ni esperar instrucciones adicionales**.

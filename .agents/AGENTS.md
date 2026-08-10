# Reglas del Proyecto - Sistema de Inversores

## Control de Versiones Obligatorio
- Cada vez que se realice un cambio en el código o configuración del sistema (incluso si es mínimo) que requiera que los usuarios o PCs descarguen o actualicen la carpeta del proyecto, **se DEBE incrementar el número de versión en `version.json`** (por ejemplo: de `2.0.0` a `2.0.1` o `2.1.0`).
- Se debe actualizar `version.json` tanto en la raíz como en la carpeta portable `Captura_Inversores_Deploy/`.
- Esto garantizará que el verificador automático de versión (`providers/version_checker.py`) notifique de inmediato con la ventana emergente Pop-Up a cualquier PC que tenga una versión anterior para que descargue la nueva actualización desde GitHub.

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


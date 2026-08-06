-- ====================================================================
-- Voltguard / Sistema de Inversores - Esquema Supabase (PostgreSQL)
-- Ejecuta este script en el SQL Editor de tu proyecto en Supabase
-- ====================================================================

-- 1. Tabla de Plantas / Monitores (Growatt, ShineMonitor, Values)
CREATE TABLE IF NOT EXISTS public.monitors_plants (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL,                  -- 'growatt', 'shinemonitor', 'values'
    plant_id TEXT NOT NULL,                  -- ID original de la planta o monitor
    name TEXT,                               -- Nombre de la planta
    metadata JSONB DEFAULT '{}'::jsonb,      -- Información adicional (dirección, tipo, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_provider_plant UNIQUE (provider, plant_id)
);

-- 2. Tabla de Dispositivos / Inversores por Planta
CREATE TABLE IF NOT EXISTS public.devices (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL,                  -- 'growatt', 'shinemonitor', 'values'
    plant_id TEXT NOT NULL,                  -- ID de planta referenciada
    device_key TEXT NOT NULL,                -- Clave única del dispositivo (ej: '214436_RBS_01')
    device_name TEXT,                        -- Nombre legible del inversor
    device_type TEXT,                        -- Tipo (Inverter, Battery, Grid, etc.)
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_provider_device_key UNIQUE (provider, device_key)
);

-- 3. Tabla de Lecturas Telemétricas (Series de Tiempo con Métricas JSONB)
CREATE TABLE IF NOT EXISTS public.telemetry_readings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL,                  -- 'growatt', 'shinemonitor', 'values'
    device_key TEXT NOT NULL,                -- Clave del dispositivo / monitor
    update_time TEXT,                        -- Estampa de tiempo del portal (ej: '2026-08-06 18:30:00')
    status TEXT,                             -- Estado de conexión o funcionamiento (ej: 'Normal', 'Desconectado')
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb, -- Métricas dinámicas (voltajes, frecuencias, corrientes)
    inserted_at TIMESTAMPTZ DEFAULT NOW()    -- Fecha y hora de captura en el sistema
);

-- Índices para búsquedas ultrarrápidas por dispositivo y tiempo
CREATE INDEX IF NOT EXISTS idx_telemetry_provider_device_inserted 
    ON public.telemetry_readings (provider, device_key, inserted_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_inserted_at 
    ON public.telemetry_readings (inserted_at DESC);

-- 4. Tabla de Eventos e Incidencias por Planta
CREATE TABLE IF NOT EXISTS public.plant_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL,                  -- 'growatt', 'shinemonitor', 'values'
    plant_id TEXT,                           -- ID de la planta o monitor
    event_type TEXT,                         -- Tipo de evento (ej: 'NO_INVERTER', 'LOGIN_ERROR', 'ALARM')
    message TEXT,                            -- Descripción del evento
    inserted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plant_events_inserted 
    ON public.plant_events (inserted_at DESC);

-- 5. Habilitar Row Level Security (RLS) opcionalmente (Permitir lectura pública/anon si se requiere)
ALTER TABLE public.monitors_plants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.telemetry_readings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plant_events ENABLE ROW LEVEL SECURITY;

-- Políticas por defecto (Permitir todo a usuarios autenticados o service_role, y lectura con anon)
CREATE POLICY "Permitir lectura publica de telemetria" ON public.telemetry_readings FOR SELECT USING (true);
CREATE POLICY "Permitir insercion total de telemetria" ON public.telemetry_readings FOR INSERT WITH CHECK (true);

CREATE POLICY "Permitir lectura publica de dispositivos" ON public.devices FOR SELECT USING (true);
CREATE POLICY "Permitir insercion total de dispositivos" ON public.devices FOR ALL USING (true);

CREATE POLICY "Permitir lectura publica de plantas" ON public.monitors_plants FOR SELECT USING (true);
CREATE POLICY "Permitir insercion total de plantas" ON public.monitors_plants FOR ALL USING (true);

CREATE POLICY "Permitir lectura publica de eventos" ON public.plant_events FOR SELECT USING (true);
CREATE POLICY "Permitir insercion total de eventos" ON public.plant_events FOR ALL USING (true);

-- Instala o activa el Bucket de Almacenamiento en Supabase Storage (Nombre: 'reports')
-- Puedes crearlo manualmente desde la consola de Supabase -> Storage -> New Bucket -> 'reports' (Public o Private con Signed URLs).

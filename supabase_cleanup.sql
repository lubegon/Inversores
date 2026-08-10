-- ====================================================================
-- Voltguard / Sistema de Inversores - Purga y Limpieza Automática Supabase
-- Ejecuta este script en el SQL Editor de tu proyecto en Supabase
-- ====================================================================

-- 1. Función para eliminar lecturas telemétricas y eventos antiguos
CREATE OR REPLACE FUNCTION public.clean_old_telemetry_readings(days_to_keep INT DEFAULT 3)
RETURNS VOID AS $$
BEGIN
    -- Eliminar lecturas con antigüedad mayor a 'days_to_keep' días
    DELETE FROM public.telemetry_readings
    WHERE inserted_at < NOW() - (days_to_keep || ' days')::INTERVAL;

    -- Eliminar eventos de planta antiguos
    DELETE FROM public.plant_events
    WHERE inserted_at < NOW() - (days_to_keep || ' days')::INTERVAL;

    RAISE NOTICE 'Limpieza completada: Eliminados registros con más de % días de antigüedad', days_to_keep;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- 2. (Opcional) Programar ejecución automática con pg_cron cada 3 días a las 00:00 UTC
-- Nota: Requiere que la extensión pg_cron esté activada en Supabase (Database -> Extensions -> pg_cron)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        -- Desprogramar trabajo previo si existe
        PERFORM cron.unschedule('purge-telemetry-every-3-days');
        
        -- Programar purga automática cada 3 días
        PERFORM cron.schedule(
            'purge-telemetry-every-3-days',
            '0 0 */3 * *',
            $$SELECT public.clean_old_telemetry_readings(3);$$
        );
        RAISE NOTICE 'pg_cron: Trabajo de purga programado exitosamente cada 3 días.';
    ELSE
        RAISE NOTICE 'pg_cron no disponible. La purga también es invocada automáticamente desde el cliente Python.';
    END IF;
END $$;

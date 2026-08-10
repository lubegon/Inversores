import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Asegurar que la raíz del proyecto está en el PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from providers.supabase_client import get_supabase, log_supabase_activity

def validate_and_correct_supabase():
    print("Iniciando validación y corrección de datos en Supabase...", flush=True)
    sb = get_supabase()
    if not sb.is_enabled():
        print("Supabase no está configurado o habilitado.")
        return {"ok": False, "error": "Supabase desactivado"}

    client = sb._client
    if not client:
        return {"ok": False, "error": "No hay cliente de Supabase"}

    results = {
        "deleted_empty_readings": 0,
        "deleted_duplicate_readings": 0,
        "fixed_stuck_events": 0
    }

    try:
        # 1. Buscar lecturas de "OK" pero que tienen puro voltaje en 0 (lecturas falsas o vacías)
        today_iso = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        response = client.table("telemetry_readings") \
            .select("id, metrics") \
            .eq("status", "OK") \
            .gte("inserted_at", today_iso) \
            .execute()
        
        to_delete = []
        for row in response.data:
            metrics = row.get("metrics") or {}
            is_empty = False
            # Si es shinemonitor, checamos voltajes
            if "r_voltage" in metrics:
                if (metrics.get("r_voltage") or 0) == 0 and (metrics.get("s_voltage") or 0) == 0 and (metrics.get("t_voltage") or 0) == 0:
                    is_empty = True
            
            if is_empty:
                to_delete.append(row["id"])

        if to_delete:
            print(f"Eliminando {len(to_delete)} lecturas de telemetría 'OK' pero con voltajes en 0...")
            for i in range(0, len(to_delete), 100):
                chunk = to_delete[i:i+100]
                client.table("telemetry_readings").delete().in_("id", chunk).execute()
            results["deleted_empty_readings"] = len(to_delete)

        # 2. Eliminar reportes de GRID_ERROR o ERROR de la tabla events que ya no aplican?
        # Por ahora limpiamos registros muy antiguos (más de 7 días).
        print("Limpiando registros antiguos generales...")
        sb.clean_old_records(days_to_keep=7)

        # Log
        log_supabase_activity("validate_db", "success", f"Se eliminaron {results['deleted_empty_readings']} lecturas vacías.", "webui")
        print("Validación completada.")
        return {"ok": True, "results": results}

    except Exception as e:
        print(f"Error durante validación: {e}")
        log_supabase_activity("validate_db", "error", str(e), "webui")
        return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    validate_and_correct_supabase()

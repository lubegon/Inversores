#!/usr/bin/env python3
"""Script de migración para transferir datos históricos desde las bases de datos SQLite locales
(Voltage Growatt.sqlite, Voltage Shinemonitor.sqlite, Voltage Values.sqlite) hacia Supabase PostgreSQL.

Uso:
    python migrate_to_supabase.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from dotenv import load_dotenv

# Cargar entorno
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

from providers.supabase_client import get_supabase


def _find_db(base_dir: Path, filename: str) -> Path | None:
    path1 = base_dir / filename
    if path1.exists():
        return path1
    path2 = base_dir / "Captura_Inversores_Deploy" / filename
    if path2.exists():
        return path2
    return None


def migrate_growatt(base_dir: Path) -> None:
    db_file = _find_db(base_dir, "Voltage Growatt.sqlite")
    if not db_file:
        print(f"[Growatt] Archivo Voltage Growatt.sqlite no encontrado. Omitiendo.")
        return

    print(f"\n--- Migrando {db_file.name} a Supabase ---")
    sb = get_supabase()
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'g_%'").fetchall()]
    print(f"[Growatt] Encontradas {len(tables)} tablas de inversores.")

    sb.save_plant("growatt", "growatt_main", "Planta Principal Growatt")

    total_rows = 0
    for t in tables:
        sb.save_device("growatt", "growatt_main", t, device_name=t)
        cols_info = [r[1] for r in cursor.execute(f'PRAGMA table_info("{t}")').fetchall()]
        
        has_inserted = "inserted_at" in cols_info
        query = f'SELECT update_time, connection_status, battery_voltage, pv1_pv2_voltage, pv1_pv2_recharging_current, total_charge_current, ac_input_voltage_frequency, ac_output_voltage_frequency{" , inserted_at" if has_inserted else ""} FROM "{t}"'
        
        rows = cursor.execute(query).fetchall()
        for r in rows:
            metrics = {
                "battery_voltage": r[2] or "",
                "pv1_pv2_voltage": r[3] or "",
                "pv1_pv2_recharging_current": r[4] or "",
                "total_charge_current": r[5] or "",
                "ac_input_voltage_frequency": r[6] or "",
                "ac_output_voltage_frequency": r[7] or "",
            }
            inserted_at = r[8] if has_inserted and len(r) > 8 else None
            sb.save_telemetry_reading(
                provider="growatt",
                device_key=t,
                update_time=r[0] or "",
                status=r[1] or "",
                metrics=metrics,
                inserted_at=inserted_at,
            )
            total_rows += 1

    conn.close()
    print(f"[Growatt] Migración completada: {total_rows} filas transferidas.")


def migrate_shinemonitor(base_dir: Path) -> None:
    db_file = _find_db(base_dir, "Voltage  Shinemonitor.sqlite")
    if not db_file:
        print(f"[ShineMonitor] Archivo Voltage  Shinemonitor.sqlite no encontrado. Omitiendo.")
        return

    print(f"\n--- Migrando {db_file.name} a Supabase ---")
    sb = get_supabase()
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # 1. Migrar Plantas
    plants = cursor.execute("SELECT plant_id, plant_name FROM meta_plants").fetchall() if _table_exists(cursor, "meta_plants") else []
    for p in plants:
        sb.save_plant("shinemonitor", str(p[0]), p[1] or str(p[0]))

    # 2. Migrar Dispositivos
    devices = cursor.execute("SELECT device_key, plant_id, device_name, table_name FROM meta_devices").fetchall() if _table_exists(cursor, "meta_devices") else []
    for d in devices:
        sb.save_device("shinemonitor", str(d[1]), str(d[0]), d[2] or str(d[0]), metadata={"table_name": d[3]})

    # 3. Migrar Eventos
    events = cursor.execute("SELECT captured_at, plant_id, status, status_detail FROM plant_events").fetchall() if _table_exists(cursor, "plant_events") else []
    for e in events:
        sb.save_plant_event("shinemonitor", str(e[1]), e[2] or "EVENT", e[3] or "", inserted_at=e[0])

    # 4. Migrar Lecturas por Tabla de Dispositivo
    all_tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    skip = {"meta_plants", "meta_devices", "plant_events", "sqlite_sequence"}
    device_tables = [t for t in all_tables if t not in skip]

    total_rows = 0
    for t in device_tables:
        cols = [r[1] for r in cursor.execute(f'PRAGMA table_info("{t}")').fetchall()]
        rows = cursor.execute(f'SELECT * FROM "{t}"').fetchall()
        for r in rows:
            row_dict = dict(zip(cols, r))
            dev_k = row_dict.get("device_key") or t
            captured_at = row_dict.get("captured_at")
            update_time = row_dict.get("Data Details Update Time") or row_dict.get("update_time") or ""
            status = row_dict.get("status") or "Normal"
            
            # Limpiar llaves meta para metrics
            metrics = {k: v for k, v in row_dict.items() if k not in ("id", "captured_at", "plant_id", "plant_name", "device_name", "device_key", "status", "status_detail")}
            
            sb.save_telemetry_reading(
                provider="shinemonitor",
                device_key=dev_k,
                update_time=str(update_time),
                status=str(status),
                metrics=metrics,
                inserted_at=str(captured_at) if captured_at else None,
            )
            total_rows += 1

    conn.close()
    print(f"[ShineMonitor] Migración completada: {total_rows} filas transferidas.")


def migrate_values(base_dir: Path) -> None:
    db_file = _find_db(base_dir, "Voltage  Values.sqlite")
    if not db_file:
        print(f"[Values] Archivo Voltage  Values.sqlite no encontrado. Omitiendo.")
        return

    print(f"\n--- Migrando {db_file.name} a Supabase ---")
    sb = get_supabase()
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    monitors = cursor.execute("SELECT monitor_key, monitor_name, table_name FROM meta_monitors").fetchall() if _table_exists(cursor, "meta_monitors") else []
    for m in monitors:
        sb.save_plant("values", str(m[0]), m[1] or str(m[0]))
        sb.save_device("values", str(m[0]), str(m[0]), m[1] or str(m[0]), metadata={"table_name": m[2]})

    all_tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'm_%'").fetchall()]
    
    total_rows = 0
    for t in all_tables:
        cols = [r[1] for r in cursor.execute(f'PRAGMA table_info("{t}")').fetchall()]
        rows = cursor.execute(f'SELECT * FROM "{t}"').fetchall()
        for r in rows:
            row_dict = dict(zip(cols, r))
            captured_at = row_dict.get("captured_at")
            row_ts = row_dict.get("row_timestamp") or ""
            metrics = {k: v for k, v in row_dict.items() if k not in ("id", "captured_at", "row_timestamp")}
            
            sb.save_telemetry_reading(
                provider="values",
                device_key=t,
                update_time=str(row_ts),
                status="Normal",
                metrics=metrics,
                inserted_at=str(captured_at) if captured_at else None,
            )
            total_rows += 1

    conn.close()
    print(f"[Values] Migración completada: {total_rows} filas transferidas.")


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    res = cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
    return res is not None


def main() -> None:
    sb = get_supabase()
    if not sb.is_enabled():
        print("ERROR: Supabase no está configurado.")
        print("Por favor agrega SUPABASE_URL y SUPABASE_KEY en tu archivo .env antes de ejecutar la migración.")
        sys.exit(1)

    print("Iniciando migración masiva de bases de datos SQLite hacia Supabase...")
    migrate_growatt(BASE_DIR)
    migrate_shinemonitor(BASE_DIR)
    migrate_values(BASE_DIR)
    print("\n¡Proceso de migración a Supabase finalizado con éxito!")


if __name__ == "__main__":
    main()

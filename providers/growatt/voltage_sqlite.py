from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


DB_FILENAME = "Voltage Growatt.sqlite"

# Orden de columnas (igual al proyecto en producción)
GROWATT_COLUMNS: list[str] = [
    "update_time",
    "connection_status",
    "battery_voltage",
    "pv1_pv2_voltage",
    "pv1_pv2_recharging_current",
    "total_charge_current",
    "ac_input_voltage_frequency",
    "ac_output_voltage_frequency",
    "inserted_at",
]


def db_path(base_dir: Path) -> Path:
    return base_dir / DB_FILENAME


def connect_db(base_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path(base_dir)))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _safe_identifier(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "x"


def _stable_suffix(value: str, *, length: int = 8) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[: max(4, length)]


def monitor_table_name(monitor_name: str) -> str:
    base = _safe_identifier(monitor_name)
    return f"g_{base}_{_stable_suffix(monitor_name)}"


def ensure_monitor_table(conn: sqlite3.Connection, *, table_name: str) -> None:
    cols_sql = ",\n            ".join([f'"{c}" TEXT' for c in GROWATT_COLUMNS])
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {cols_sql}
        )
        """
    )

    # Compat: si la tabla existía sin inserted_at.
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    existing = {r[1] for r in rows}
    if "inserted_at" not in existing:
        conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "inserted_at" TEXT')


def _now_inserted_at() -> str:
    # Intentar America/Caracas (sin depender de pytz)
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Caracas")
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def insert_monitor_row(conn: sqlite3.Connection, *, table_name: str, row: Iterable[str]) -> None:
    values = list(row)
    if len(values) == len(GROWATT_COLUMNS) - 1:
        values.append(_now_inserted_at())
    elif len(values) == len(GROWATT_COLUMNS):
        if not str(values[-1] or "").strip():
            values[-1] = _now_inserted_at()
    else:
        raise ValueError(
            f"Row length {len(values)} does not match expected columns {len(GROWATT_COLUMNS)}"
        )

    placeholders = ", ".join(["?"] * len(GROWATT_COLUMNS))
    columns_sql = ", ".join([f'"{c}"' for c in GROWATT_COLUMNS])
    conn.execute(
        f'INSERT INTO "{table_name}" ({columns_sql}) VALUES ({placeholders})',
        values,
    )


@dataclass(frozen=True)
class GrowattRow:
    update_time: str = ""
    connection_status: str = ""
    battery_voltage: str = ""
    pv1_pv2_voltage: str = ""
    pv1_pv2_recharging_current: str = ""
    total_charge_current: str = ""
    ac_input_voltage_frequency: str = ""
    ac_output_voltage_frequency: str = ""

    def as_list(self) -> list[str]:
        return [
            self.update_time,
            self.connection_status,
            self.battery_voltage,
            self.pv1_pv2_voltage,
            self.pv1_pv2_recharging_current,
            self.total_charge_current,
            self.ac_input_voltage_frequency,
            self.ac_output_voltage_frequency,
        ]

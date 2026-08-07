from __future__ import annotations

import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

# Intentar importar la librería oficial de Supabase
try:
    from supabase import Client, create_client
    HAS_SUPABASE_LIB = True
except ImportError:
    HAS_SUPABASE_LIB = False
    Client = Any

logger = logging.getLogger("supabase_client")


def _get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "Desconocida"


def _get_version() -> str:
    try:
        from providers.version_checker import get_local_version
        return get_local_version()
    except Exception:
        return "1.0.0"


class SupabaseManager:
    """Administrador centralizado de la conexion e ingesta de datos con Supabase.
    
    Diseno resiliente: Si Supabase no esta configurado o la red falla, los metodos
    capturan los errores de forma silenciosa/logueada para que los scrapers continúen
    su ejecucion guardando en SQLite local.
    """

    _instance: SupabaseManager | None = None

    def __init__(self) -> None:
        self.url: str = os.getenv("SUPABASE_URL", "").strip()
        self.key: str = os.getenv("SUPABASE_KEY", "").strip()
        self.bucket_name: str = os.getenv("SUPABASE_STORAGE_BUCKET", "reports").strip()
        self.client: Client | None = None
        self._disabled_warned: bool = False

        if HAS_SUPABASE_LIB and self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
            except Exception as exc:
                logger.warning(f"No se pudo inicializar el cliente Supabase: {exc}")

    @classmethod
    def get_instance(cls) -> SupabaseManager:
        if cls._instance is None:
            cls._instance = SupabaseManager()
        return cls._instance

    def is_enabled(self) -> bool:
        return self.client is not None

    def _log_unconfigured_once(self) -> None:
        pass

    def save_plant(
        self,
        provider: str,
        plant_id: str,
        name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        try:
            meta = dict(metadata or {})
            if "hostname" not in meta:
                meta["hostname"] = _get_hostname()
            payload = {
                "provider": provider,
                "plant_id": str(plant_id),
                "name": name or str(plant_id),
                "metadata": meta,
                "updated_at": datetime.now().isoformat(),
            }
            self.client.table("monitors_plants").upsert(
                payload, on_conflict="provider,plant_id"
            ).execute()
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando planta ({provider}/{plant_id}): {exc}")
            return False

    def save_device(
        self,
        provider: str,
        plant_id: str,
        device_key: str,
        device_name: str = "",
        device_type: str = "Inverter",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        try:
            meta = dict(metadata or {})
            if "hostname" not in meta:
                meta["hostname"] = _get_hostname()
            payload = {
                "provider": provider,
                "plant_id": str(plant_id),
                "device_key": str(device_key),
                "device_name": device_name or str(device_key),
                "device_type": device_type,
                "metadata": meta,
            }
            self.client.table("devices").upsert(
                payload, on_conflict="provider,device_key"
            ).execute()
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando dispositivo ({device_key}): {exc}")
            return False

    def save_telemetry_reading(
        self,
        provider: str,
        device_key: str,
        update_time: str,
        status: str = "",
        metrics: dict[str, Any] | None = None,
        inserted_at: str | None = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        try:
            m = dict(metrics or {})
            if "client_host" not in m:
                m["client_host"] = _get_hostname()
            if "client_version" not in m:
                m["client_version"] = _get_version()

            payload = {
                "provider": provider,
                "device_key": str(device_key),
                "update_time": update_time or "",
                "status": status or "",
                "metrics": m,
            }
            if inserted_at:
                payload["inserted_at"] = inserted_at

            self.client.table("telemetry_readings").insert(payload).execute()
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando lectura telemétrica ({device_key}): {exc}")
            return False

    def save_plant_event(
        self,
        provider: str,
        plant_id: str,
        event_type: str,
        message: str,
        inserted_at: str | None = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        try:
            payload = {
                "provider": provider,
                "plant_id": str(plant_id),
                "event_type": event_type,
                "message": message,
            }
            if inserted_at:
                payload["inserted_at"] = inserted_at

            self.client.table("plant_events").insert(payload).execute()
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando evento ({provider}/{plant_id}): {exc}")
            return False

    def upload_report_file(self, file_path: str | Path, destination_name: str | None = None) -> str | None:
        """Sube un archivo de reporte (Excel/CSV) a Supabase Storage y retorna su URL publica/firmada."""
        if not self.is_enabled():
            return None
        path = Path(file_path)
        if not path.exists():
            logger.error(f"[Supabase Storage] Archivo no existe: {file_path}")
            return None

        dest = destination_name or path.name
        try:
            with open(path, "rb") as f:
                file_bytes = f.read()

            # Subir o reemplazar archivo en el bucket
            res = self.client.storage.from_(self.bucket_name).upload(
                path=dest,
                file=file_bytes,
                file_options={"upsert": "true", "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
            )

            # Intentar obtener URL publica
            public_url = self.client.storage.from_(self.bucket_name).get_public_url(dest)
            logger.info(f"[Supabase Storage] Reporte subido exitosamente: {public_url}")
            return public_url
        except Exception as exc:
            logger.error(f"[Supabase Storage] Error subiendo reporte {dest}: {exc}")
            return None

    def get_report_download_url(self, filename: str) -> str | None:
        """Obtiene la URL de descarga para un reporte en Supabase Storage."""
        if not self.is_enabled():
            return None
        try:
            # Primero intentar Signed URL de 1 hora
            signed = self.client.storage.from_(self.bucket_name).create_signed_url(filename, 3600)
            if signed and isinstance(signed, dict) and "signedUrl" in signed:
                return signed["signedUrl"]
            # Fallback a URL pública
            return self.client.storage.from_(self.bucket_name).get_public_url(filename)
        except Exception as exc:
            logger.error(f"[Supabase Storage] Error obteniendo URL para {filename}: {exc}")
            return None


# Singleton conveniente para usar en todo el proyecto
get_supabase = SupabaseManager.get_instance


def save_plant(*args, **kwargs) -> bool:
    return SupabaseManager.get_instance().save_plant(*args, **kwargs)


def save_device(*args, **kwargs) -> bool:
    return SupabaseManager.get_instance().save_device(*args, **kwargs)


def save_telemetry_reading(*args, **kwargs) -> bool:
    return SupabaseManager.get_instance().save_telemetry_reading(*args, **kwargs)


def save_plant_event(*args, **kwargs) -> bool:
    return SupabaseManager.get_instance().save_plant_event(*args, **kwargs)
<<<<<<< HEAD
=======

>>>>>>> 4f3ebe3 (fix: exportar funciones auxiliares save_plant/save_device/save_telemetry_reading en supabase_client)

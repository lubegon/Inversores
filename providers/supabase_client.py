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

import json
import ssl
import urllib.request

logger = logging.getLogger("supabase_client")


def _get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "Desconocida"


def _get_client_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_version() -> str:
    try:
        from providers.version_checker import get_local_version
        return get_local_version()
    except Exception:
        return "1.0.0"


DEFAULT_SUPABASE_URL = "https://zlepfamahxmplaocfcwx.supabase.co"
DEFAULT_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpsZXBmYW1haHhtcGxhb2NmY3d4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYwNDcwMDMsImV4cCI6MjEwMTYyMzAwM30.JU3NLceSoNLZ434yM0X32IQjPB5_RLG_wEArTQ7EFHE"


class _UrllibSupabaseTable:
    def __init__(self, manager: SupabaseManager, table_name: str) -> None:
        self.manager = manager
        self.table_name = table_name
        self._payload: Any = None
        self._action: str = "POST"
        self._on_conflict: str | None = None

    def insert(self, payload: dict | list) -> _UrllibSupabaseTable:
        self._payload = payload
        self._action = "POST"
        self._on_conflict = None
        return self

    def upsert(self, payload: dict | list, on_conflict: str | None = None) -> _UrllibSupabaseTable:
        self._payload = payload
        self._action = "POST"
        self._on_conflict = on_conflict
        return self

    def execute(self) -> Any:
        url = f"{self.manager.url.rstrip('/')}/rest/v1/{self.table_name}"
        if self._on_conflict:
            url += f"?on_conflict={self._on_conflict}"

        headers = {
            "apikey": self.manager.key,
            "Authorization": f"Bearer {self.manager.key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates" if self._on_conflict else "return=minimal",
        }

        data_bytes = json.dumps([self._payload] if isinstance(self._payload, dict) else self._payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=self._action)

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.read()


class _UrllibSupabaseClient:
    def __init__(self, manager: SupabaseManager) -> None:
        self.manager = manager

    def table(self, table_name: str) -> _UrllibSupabaseTable:
        return _UrllibSupabaseTable(self.manager, table_name)


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

        if not self.url or "tu-proyecto" in self.url:
            self.url = DEFAULT_SUPABASE_URL
        if not self.key or "tu-anon" in self.key:
            self.key = DEFAULT_SUPABASE_KEY

        self.client: Any = None
        self._disabled_warned: bool = False

        if HAS_SUPABASE_LIB and self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
            except Exception as exc:
                logger.warning(f"No se pudo inicializar cliente Supabase oficial: {exc}")
                self.client = _UrllibSupabaseClient(self)
        elif self.url and self.key:
            self.client = _UrllibSupabaseClient(self)

    @classmethod
    def get_instance(cls) -> SupabaseManager:
        if cls._instance is None or cls._instance.client is None:
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
        provider: str = "unknown",
        device_key: str = "",
        update_time: str = "",
        status: str = "",
        metrics: dict[str, Any] | None = None,
        inserted_at: str | None = None,
        plant_id: str | None = None,
        raw_data: Any = None,
        **kwargs: Any,
    ) -> bool:
        if not self.is_enabled():
            return False
        try:
            m = dict(metrics or {})
            if "client_host" not in m:
                m["client_host"] = _get_hostname()
            if "client_version" not in m:
                m["client_version"] = _get_version()
            if "client_ip" not in m:
                m["client_ip"] = _get_client_ip()
            if plant_id and "plant_id" not in m:
                m["plant_id"] = str(plant_id)

            prov = provider or kwargs.get("provider", "unknown")
            dev_k = device_key or kwargs.get("device_key", "")

            payload = {
                "provider": prov,
                "device_key": str(dev_k),
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

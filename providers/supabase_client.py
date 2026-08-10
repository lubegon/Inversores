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


def log_supabase_activity(action: str, status: str, detail: str, provider: str = "webui") -> None:
    try:
        storage_dir = Path(__file__).resolve().parent.parent / "storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        path = storage_dir / "supabase_activity.jsonl"
        rec = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "status": status,
            "detail": detail,
            "provider": provider,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Registrar también en el Logger del Sistema (.bat a cierre)
        try:
            from providers.system_logger import log_sys_event
            level = "ERROR" if status == "error" else "INFO"
            log_sys_event(level, "SUPABASE", f"[{action.upper()}] {detail} ({provider})")
        except Exception:
            pass
    except Exception:
        pass


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
        # service_role key omite RLS — necesario para Storage si el bucket no es público
        self.service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
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

        # Si hay service_role key disponible, intentar hacer el bucket público al inicio
        if self.client and self.service_key:
            self._ensure_bucket_public()

    @classmethod
    def get_instance(cls) -> SupabaseManager:
        if cls._instance is None or cls._instance.client is None:
            cls._instance = SupabaseManager()
        return cls._instance

    def is_enabled(self) -> bool:
        return self.client is not None

    @property
    def _storage_auth_key(self) -> str:
        """Retorna el mejor key disponible para operaciones de Storage.
        Prefiere service_role (sin RLS) sobre anon (con RLS)."""
        return self.service_key if self.service_key else self.key

    def _ensure_bucket_public(self) -> None:
        """Intenta hacer el bucket público usando el service_role key.
        Solo se ejecuta si service_key está disponible."""
        if not self.service_key:
            return
        try:
            import urllib.request as _r, ssl as _s, json as _j
            ctx = _s._create_unverified_context()
            body = _j.dumps({"public": True}).encode()
            req = _r.Request(
                f"{self.url.rstrip('/')}/storage/v1/bucket/{self.bucket_name}",
                data=body, method="PUT",
                headers={
                    "Authorization": f"Bearer {self.service_key}",
                    "apikey": self.service_key,
                    "Content-Type": "application/json",
                },
            )
            with _r.urlopen(req, timeout=8, context=ctx):
                logger.info(f"[Supabase Storage] Bucket '{self.bucket_name}' configurado como público.")
        except Exception:
            pass  # Silencioso — no es crítico

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
            log_supabase_activity("upsert_plant", "success", f"Planta {name or plant_id} guardada", provider)
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando planta ({provider}/{plant_id}): {exc}")
            log_supabase_activity("upsert_plant", "error", f"Planta {name or plant_id}: {exc}", provider)
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
            log_supabase_activity("upsert_device", "success", f"Dispositivo {device_name or device_key} guardado", provider)
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando dispositivo ({device_key}): {exc}")
            log_supabase_activity("upsert_device", "error", f"Dispositivo {device_key}: {exc}", provider)
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
            log_supabase_activity("insert_telemetry", "success", f"Lectura de {dev_k} enviada", prov)
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando lectura telemétrica ({device_key}): {exc}")
            log_supabase_activity("insert_telemetry", "error", f"Dispositivo {device_key}: {exc}", provider)
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
            log_supabase_activity("insert_event", "success", f"Evento de planta {plant_id}: {message}", provider)
            return True
        except Exception as exc:
            logger.error(f"[Supabase] Error guardando evento ({provider}/{plant_id}): {exc}")
            log_supabase_activity("insert_event", "error", f"Planta {plant_id}: {exc}", provider)
            return False

    def upload_report_file(self, file_path: str | Path, destination_name: str | None = None) -> str | None:
        """Sube un archivo de reporte (Excel/CSV) a Supabase Storage y retorna su URL publica/firmada.

        Usa la API REST directa de Supabase Storage con x-upsert:true para evitar
        problemas de RLS del SDK de Python. Hace fallback al SDK si falla.
        """
        if not self.is_enabled():
            return None
        path = Path(file_path)
        if not path.exists():
            logger.error(f"[Supabase Storage] Archivo no existe: {file_path}")
            log_supabase_activity("upload_report", "error", f"Archivo no existe: {file_path}", "webui")
            return None

        dest = destination_name or path.name
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if str(dest).lower().endswith(".csv"):
            content_type = "text/csv"

        try:
            with open(path, "rb") as f:
                file_bytes = f.read()

            # Método 1: REST directo con urllib (evita bugs de RLS del SDK)
            import urllib.request as _urlreq
            import ssl as _ssl
            storage_url = f"{self.url.rstrip('/')}/storage/v1/object/{self.bucket_name}/{dest}"
            req = _urlreq.Request(
                storage_url,
                data=file_bytes,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._storage_auth_key}",
                    "apikey": self._storage_auth_key,
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
            )
            ctx = _ssl._create_unverified_context()
            try:
                with _urlreq.urlopen(req, timeout=20, context=ctx) as resp:
                    resp.read()  # consumir respuesta
            except Exception as rest_err:
                # Si el REST falla (ej: 400 ya existe), intentar PUT para upsert
                if "400" in str(rest_err) or "already" in str(rest_err).lower():
                    req2 = _urlreq.Request(
                        storage_url,
                        data=file_bytes,
                        method="PUT",
                        headers={
                            "Authorization": f"Bearer {self._storage_auth_key}",
                            "apikey": self._storage_auth_key,
                            "Content-Type": content_type,
                            "x-upsert": "true",
                        },
                    )
                    with _urlreq.urlopen(req2, timeout=20, context=ctx) as resp2:
                        resp2.read()
                else:
                    raise

            # Obtener URL pública
            public_url = self.client.storage.from_(self.bucket_name).get_public_url(dest)
            logger.info(f"[Supabase Storage] Reporte subido exitosamente: {public_url}")
            log_supabase_activity("upload_report", "success", f"Reporte {dest} subido", "webui")
            return public_url
        except Exception as exc:
            err_msg = str(exc)
            if "403" in err_msg or "Unauthorized" in err_msg or "row-level security" in err_msg.lower():
                logger.error(
                    f"[Supabase Storage] Error 403 RLS al subir '{dest}'. "
                    f"Ve a Supabase Dashboard → Storage → {self.bucket_name} → Policies "
                    f"y agrega una policy de INSERT para el rol 'anon'."
                )
                log_supabase_activity("upload_report", "error", f"Error 403 RLS: el bucket '{self.bucket_name}' requiere policy de INSERT para anon", "webui")
            else:
                logger.error(f"[Supabase Storage] Error subiendo reporte {dest}: {exc}")
                log_supabase_activity("upload_report", "error", f"Error subiendo {dest}: {exc}", "webui")
            return None

    def get_report_download_url(self, filename: str) -> str | None:
        """Obtiene la URL de descarga para un reporte en Supabase Storage."""
        if not self.is_enabled():
            return None
        try:
            # Primero intentar Signed URL de 1 hora
            signed = self.client.storage.from_(self.bucket_name).create_signed_url(filename, 3600)
            if signed and isinstance(signed, dict) and "signedUrl" in signed:
                log_supabase_activity("get_download_url", "success", f"Generada URL firmada para {filename}", "webui")
                return signed["signedUrl"]
            # Fallback a URL pública
            url = self.client.storage.from_(self.bucket_name).get_public_url(filename)
            log_supabase_activity("get_download_url", "success", f"Generada URL pública para {filename}", "webui")
            return url
        except Exception as exc:
            logger.error(f"[Supabase Storage] Error obteniendo URL para {filename}: {exc}")
            log_supabase_activity("get_download_url", "error", f"Error obteniendo URL para {filename}: {exc}", "webui")
            return None

    def clean_old_records(self, days_to_keep: int = 3) -> bool:
        """Elimina lecturas telemétricas y eventos de Supabase con más de X días de antigüedad."""
        if not self.is_enabled():
            return False
        try:
            from datetime import timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()

            # Intentar RPC si la función almacenada existe
            try:
                self.client.rpc("clean_old_telemetry_readings", {"days_to_keep": days_to_keep}).execute()
                log_supabase_activity("cleanup", "success", f"Ejecutada función de purga RPC ({days_to_keep} días)", "system")
                return True
            except Exception:
                pass

            # Fallback vía API REST directa
            import urllib.request as _r, ssl as _s
            headers = {
                "Authorization": f"Bearer {self.service_key or self.key}",
                "apikey": self.service_key or self.key,
            }
            ctx = _s._create_unverified_context()

            # Purga en telemetry_readings
            url_tel = f"{self.url.rstrip('/')}/rest/v1/telemetry_readings?inserted_at=lt.{cutoff}"
            req_tel = _r.Request(url_tel, method="DELETE", headers=headers)
            with _r.urlopen(req_tel, timeout=10, context=ctx) as resp:
                resp.read()

            # Purga en plant_events
            url_ev = f"{self.url.rstrip('/')}/rest/v1/plant_events?inserted_at=lt.{cutoff}"
            req_ev = _r.Request(url_ev, method="DELETE", headers=headers)
            with _r.urlopen(req_ev, timeout=10, context=ctx) as resp:
                resp.read()

            log_supabase_activity("cleanup", "success", f"Purga de registros antiguos completada (anteriores a {cutoff})", "system")
            return True
        except Exception as exc:
            logger.error(f"[Supabase Cleanup] Error ejecutando purga: {exc}")
            log_supabase_activity("cleanup", "error", f"Error en purga: {exc}", "system")
            return False


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

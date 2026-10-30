---
name: entorno-windows10-restringido
description: Directrices de arquitectura, compatibilidad, despliegue y ejecución para el parque de aproximadamente 16 PCs operativas con Windows 10 y limitaciones de permisos de administrador (sin privilegios UAC). Activar siempre al crear o modificar código, scripts, navegadores, instaladores o configuraciones de este proyecto.
---

# Skill: Entorno Corporativo Windows 10 Restringido (Parque de ~16 PCs)

Esta skill es **exclusiva de este proyecto (`Sistema de inversores` / Voltguard)** y define las condiciones reales de hardware, sistema operativo y permisos en las que opera el sistema.

## 1. Condiciones Operativas Invariables del Proyecto

1. **El sistema corre en PCs con limitación de administrador:**
   - Los usuarios operativos trabajan en sesiones estándar de Windows **sin permisos de Administrador local ni elevación UAC**.
   - Está estrictamente prohibido requerir instalaciones en `C:\Program Files`, escrituras en `HKEY_LOCAL_MACHINE` (`HKLM`), creación de servicios de Windows (`sc create`), instalación de drivers o uso de gestores de paquetes globales que pidan elevación.
   - Todo debe ejecutarse en espacio de usuario (`set "__COMPAT_LAYER=RunAsInvoker"`) y desde carpetas locales portables (`.venv`, `playwright_browsers/`, `Captura_Inversores_Deploy/`).
   - **Detección de Python sin caer en el stub de Microsoft Store:** En Windows 10 sin administrador, `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` es un ejecutable vacío (0 bytes) que falla con código `9009`. `iniciar_servidor.bat` debe priorizar `.venv\Scripts\python.exe`, filtrar `WindowsApps` al usar `where python`, e invocar siempre `"%PY_EXE%"` con ruta absoluta entrecomillada.

2. **Son aproximadamente 16 PCs operativas (Arquitectura Híbrida Local-First):**
   - El sistema opera de forma distribuida en un parque de **~16 computadoras** conectadas a la base de datos central en Supabase.
   - **Por qué es indispensable la Arquitectura Híbrida (SQLite Local + Espejo Supabase):**
     - **SQLite Local Primario:** Durante el ciclo de scraping, cada PC escribe únicamente en su SQLite local (`WAL` + `busy_timeout=30000`) sin llamadas HTTP intermedias, garantizando que fallos de red nunca detengan la captura.
     - **Supabase en Segundo Plano por Lotes:** Al terminar la captura, la PC sube en lotes (`save_plants_batch`, `save_devices_batch`, `save_telemetry_readings_batch`) sus lecturas a Supabase. Así, si la PC #1 captura el turno de la Mañana y la PC #8 genera el reporte Excel en la Tarde, la PC #8 descarga automáticamente de Supabase los turnos previos sin dejar celdas vacías ni obligar a las 16 PCs a escrapear simultáneamente los portales externos.
   - **Auto-curación de `.env` en carpetas nuevas o actualizadas:** Tanto `iniciar_servidor.bat` como `providers/version_checker.py` (`perform_auto_update`) deben comprobar si `.env` no existe o contiene placeholders (`pon_tu_usuario_growatt`, `pon_tu_clave_aqui`) y repararlo automáticamente desde `.env.example`.
   - Cada cambio en el proyecto debe incrementar `version.json`, sincronizar `Captura_Inversores_Deploy/` y subirse a GitHub (`main`) para que las ~16 PCs reciban el aviso automático de actualización (`providers/version_checker.py`).

3. **El sistema operativo de todas las PCs es Windows 10:**
   - El 100% del parque (~16 PCs) ejecuta **Windows 10**.
   - **Navegador garantizado:** Todas las PCs con Windows 10 cuentan con **Microsoft Edge (`channel="msedge"`)** preinstalado de fábrica y firmado por Microsoft. Si el binario portable de Chromium es bloqueado por políticas corporativas (AppLocker/GPO), el sistema debe hacer fallback automático a `channel="msedge"`.
   - **Bypass SSL corporativo:** Mantener siempre `ignore_https_errors=True`, `PYTHONHTTPSVERIFY=0` y `FIREWALL_SAFE_ARGS` para operar sin bloqueos detrás de firewalls o antivirus corporativos en Windows 10.
   - **Exportación PDF sin drivers:** Utilizar la vista `/report-view` con `@media print` y `window.print()` para aprovechar "Microsoft Print to PDF" / Edge nativo de Windows 10 sin instalar impresoras virtuales.

---

## 2. Checklist de Verificación antes de Guardar Cambios

- [ ] ¿El cambio funciona en **Windows 10** sin pedir contraseña de **Administrador (UAC)**?
- [ ] ¿Los scripts `.bat` mantienen `set "__COMPAT_LAYER=RunAsInvoker"`, ignoran el stub `WindowsApps\python.exe` y reparan `.env` si contiene placeholders?
- [ ] ¿Es compatible con Python 3.10 portable en las **~16 PCs** sin instalar dependencias externas del sistema?
- [ ] ¿Se evitó incluir `SUPABASE_SERVICE_KEY=sb_secret_...` en `.env.example` para no bloquear el push a GitHub?
- [ ] ¿Se sincronizó `Captura_Inversores_Deploy/`, se incrementó `version.json` y se hizo `git push` para actualizar las ~16 PCs?

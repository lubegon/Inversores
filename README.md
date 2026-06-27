# Voltguardv2 (Scraping)

## Objetivo (fase 1)
Login headless a Shinemonitor con Playwright (Edge) y guardar sesión (`storageState`).

## Estructura del proyecto
- Los scripts en la raíz (`shinemonitor_*.py`) se mantienen como **wrappers** para no romper comandos existentes.
- La lógica real de ShineMonitor vive en `providers/shinemonitor/`.
- `providers/values/` y `providers/growatt/` quedan preparados para implementar/migrar esos gestores sin mezclar código.

## Requisitos
- Python 3.12
- Microsoft Edge instalado

## Setup (Windows)
1) Crear entorno virtual:

```powershell
py -3.12 -m venv .venv
```

2) Activar entorno:

```powershell
.\.venv\Scripts\Activate.ps1
```

3) Instalar dependencias:

```powershell
pip install -r requirements.txt
```

4) Instalar browsers de Playwright:

```powershell
playwright install
```

5) Crear `.env` (puedes copiar `.env.example`):

```powershell
Copy-Item .env.example .env
```

6) Ejecutar login Shinemonitor:

```powershell
python .\shinemonitor_login.py
```

7) Descubrir lista de Plants (dropdown) y guardar snapshot:

```powershell
python .\shinemonitor_discover_plants.py
```

8) Probar navegación hasta Data Details (sin extraer datos):

Puedes fijar un plant específico con `PLANT_ID` en `.env` o por consola.

```powershell
$env:PLANT_ID="214436"
python .\shinemonitor_navigate_datadetails.py
```

9) Scraping de la fila más reciente (Data Details) a SQLite:

Prueba con un solo plant:

```powershell
$env:PLANT_ID="214436"
python .\shinemonitor_scrape_voltage.py
```

Ejecución para todos los plants del snapshot:

```powershell
Remove-Item Env:PLANT_ID -ErrorAction SilentlyContinue
python .\shinemonitor_scrape_voltage.py
```

Salidas:
- `storage/shinemonitor.json`
- `storage/shinemonitor-after-login.png`
- `storage/shinemonitor-plants.json`
- `storage/shinemonitor-plants.png`
- `storage/nav/*.png`
- `storage/shinemonitor-nav-<plant_id>.json`
- `Voltage  Shinemonitor.sqlite`
- `storage/scrape/*.png`

Notas DB:
- Se crea 1 tabla por dispositivo bajo "Inverter" (si un plant tiene RBS_01 y RBS_02, quedan en tablas distintas).
- La tabla se nombra como `device_<plant_id>_<hash>` y el mapeo queda en `meta_devices` (device_key ↔ device_name ↔ table_name).
- Si un plant no tiene Inverter o no tiene dispositivos, se registra en `plant_events`.


## WebUI (local)

Levanta una interfaz web simple (sin frameworks) para ejecutar los 3 gestores (Growatt / ShineMonitor / Values) y ver logs en tiempo real.

En PowerShell:

```powershell
python .\webui_server.py
```

Por defecto queda en:

- http://127.0.0.1:8000/

Variables útiles:

- `WEBUI_HOST` (default: `127.0.0.1`)
- `WEBUI_PORT` (default: `8000`)


## Publicar GRATIS con Cloudflare Tunnel (sin servidor)

Esto te da un link público para acceder a la WebUI desde cualquier lado, mientras tu PC esté encendida y el servidor esté corriendo.

1) Inicia la WebUI:

```powershell
python .\webui_server.py
```

2) Instala `cloudflared` (elige una opción):

- Con winget:

```powershell
winget install Cloudflare.cloudflared
```

- O descarga el binario `cloudflared` desde Cloudflare y asegúrate de poder ejecutar `cloudflared --version`.

3) Abre el túnel hacia tu WebUI local:

```powershell
cd "C:\Users\duvely_huiza\OneDrive - MDS Telecom CA\Documents\Cloudflared"

.\cloudflared.exe tunnel --url http://127.0.0.1:8000
```

Te imprimirá un URL tipo `https://xxxxx.trycloudflare.com`.


## (Recomendado) Proteger la WebUI con contraseña

Si vas a exponer el link por internet, habilita Basic Auth. El navegador te pedirá usuario/contraseña.

En PowerShell:

```powershell
$env:WEBUI_BASIC_AUTH="usuario:password"
python .\webui_server.py
```

Alternativa:

```powershell
$env:WEBUI_USER="usuario"
$env:WEBUI_PASS="password"
python .\webui_server.py
```


Ejecutar Proyecto en bash

cd "/c/Users/duvely_huiza/OneDrive - MDS Telecom CA/Escritorio/Voltguardv2" && source .venv/Scripts/activate && HEADLESS=1 python -u shinemonitor_scrape_voltage.py

Values

HEADLESS=1 VALUES_FAST=1 VALUES_TURBO=1 python values_scrape_voltage.py


Levantar servicio

python webui_server.py
@echo off
rem Configurar codificacion UTF-8 para evitar caracteres extranos
chcp 65001 > nul
title Iniciar Servidor - Captura de Inversores
echo ======================================================================
echo           INICIALIZACION DEL SISTEMA - CAPTURA DE INVERSORES
echo ======================================================================
echo.

rem 1. Verificar si Python esta instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Python no esta instalado o no se encuentra en el PATH.
    echo [INFO] Iniciando descarga e instalacion automatica de Python 3.12...
    echo.
    
    echo [PASO 1/3] Descargando instalador de Python 3.12.2 desde python.org...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; echo 'Descargando...'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.2/python-3.12.2-amd64.exe' -OutFile 'python_installer.exe'"
    if errorlevel 1 (
        echo [ERROR] No se pudo descargar el instalador de Python. Asegurate de tener conexion a internet.
        pause
        exit /b 1
    )
    echo [OK] Descarga completada.
    echo.
    
    echo [PASO 2/3] Instalando Python 3.12 de forma silenciosa...
    echo [INFO] Esto tardara aproximadamente 1 minuto. Espere por favor...
    python_installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 Include_doc=0
    if errorlevel 1 (
        echo [ERROR] Fallo la instalacion de Python.
        del python_installer.exe >nul 2>&1
        pause
        exit /b 1
    )
    del python_installer.exe >nul 2>&1
    echo [OK] Instalacion completada con exito.
    echo.
    
    echo [PASO 3/3] Configurando variables de entorno temporales...
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\;%LOCALAPPDATA%\Programs\Python\Python312\Scripts\;%PATH%"
    
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] No se pudo verificar la instalacion de Python en el PATH temporal.
        echo Por favor, cierre esta ventana e intente abrir iniciar_servidor.bat de nuevo.
        pause
        exit /b 1
    )
    echo [OK] Entorno de Python configurado correctamente.
    echo.
)

rem 2. Crear o reparar entorno virtual si es necesario
set VENV_OK=1
if exist .venv (
    .venv\Scripts\python.exe --version >nul 2>&1
) else (
    set VENV_OK=0
)

rem Comprobar si la ejecucion del comando anterior fallo (indica venv roto)
if errorlevel 1 (
    echo [WARNING] El entorno virtual existente .venv esta danado o es invalido.
    echo [INFO] Reconstruyendo entorno virtual automaticamente...
    rmdir /s /q .venv
    set VENV_OK=0
)

if %VENV_OK% equ 1 goto venv_ready

echo [INFO] Creando entorno virtual .venv...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
echo [OK] Entorno virtual creado exitosamente.
echo.

:venv_ready

rem 3. Instalar dependencias dentro del entorno virtual
echo [INFO] Instalando/actualizando dependencias en el entorno virtual .venv...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [WARNING] Hubo un problema al instalar las dependencias de requirements.txt.
    echo Se intentara continuar...
    echo.
) else (
    echo [OK] Dependencias instaladas exitosamente.
    echo.
)

rem 4. Instalar navegadores de Playwright
echo [INFO] Asegurando que los navegadores de automatizacion (Playwright) esten instalados...
.venv\Scripts\playwright.exe install chromium msedge
if errorlevel 1 (
    echo [WARNING] No se pudieron instalar los navegadores de Playwright.
    echo Asegurate de tener conexion a Internet.
    echo.
) else (
    echo [OK] Navegadores listos.
    echo.
)

rem 5. Crear archivo .env si no existe
if not exist .env (
    if exist .env.example (
        echo [INFO] Creando archivo .env a partir de .env.example...
        copy .env.example .env > nul
        
        echo. >> .env
        echo # Configuracion de red local >> .env
        echo WEBUI_HOST=0.0.0.0 >> .env
        echo WEBUI_PORT=8000 >> .env
        echo [OK] Archivo .env configurado con acceso LAN por defecto.
        echo.
    ) else (
        echo [WARNING] No se encontro el archivo .env.example.
        echo Se creara un archivo .env basico...
        echo WEBUI_HOST=0.0.0.0 > .env
        echo WEBUI_PORT=8000 >> .env
        echo [OK] Archivo .env basico creado.
        echo.
    )
)

rem 6. Iniciar Servidor
echo ======================================================================
echo    Iniciando Servidor de Captura de Inversores...
echo ======================================================================
echo.
.venv\Scripts\python.exe -u webui_server.py
echo.
pause

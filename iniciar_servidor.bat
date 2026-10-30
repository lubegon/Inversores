@echo off
set "__COMPAT_LAYER=RunAsInvoker"
rem Configurar codificacion UTF-8 para evitar caracteres extranos
chcp 65001 > nul
rem Desactivar verificacion estricta SSL en Python para evitar bloqueos en Windows
set PYTHONHTTPSVERIFY=0
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0playwright_browsers"
title Iniciar Servidor - Captura de Inversores
set "SYS_VER=2.19.5"
if exist version.json (
    for /f "tokens=2 delims=:," %%A in ('findstr "version" version.json') do set "SYS_VER=%%~A"
)
set "SYS_VER=%SYS_VER: =%"
set "SYS_VER=%SYS_VER:"=%"
echo ======================================================================
echo       INICIALIZACION DEL SISTEMA - CAPTURA DE INVERSORES (v%SYS_VER%)
echo   Sistema elaborado por Duvelis Huiza y el Lic. Luis G.
echo ======================================================================
echo.

rem 1. Deteccion inteligente de Python (Priorizando .venv existente y Python 3.10)
set "PY_EXE="

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" --version >nul 2>&1
    if not errorlevel 1 (
        set "PY_EXE=%~dp0.venv\Scripts\python.exe"
        goto setup_py_path
    )
)

rem Comprobar si Python 3.10 esta instalado en rutas estandar de usuario o sistema
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    goto setup_py_path
)
if exist "C:\Python310\python.exe" (
    set "PY_EXE=C:\Python310\python.exe"
    goto setup_py_path
)
if exist "%ProgramFiles%\Python310\python.exe" (
    set "PY_EXE=%ProgramFiles%\Python310\python.exe"
    goto setup_py_path
)

rem Comprobar otras versiones de Python (3.11, 3.12, 3.x) en AppData Local
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto setup_py_path
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto setup_py_path
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" (
        set "PY_EXE=%%~fD\python.exe"
        goto setup_py_path
    )
)

rem Comprobar comando py (Windows Python Launcher) con Python 3.10 o 3.x
py -3.10 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%i in ('py -3.10 -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%i"
    if defined PY_EXE goto setup_py_path
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%i"
    if defined PY_EXE goto setup_py_path
)

rem Comprobar comando python en el PATH ignorando el stub vacio de WindowsApps
for /f "tokens=*" %%i in ('where python 2^>nul') do (
    echo "%%i" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        "%%~i" --version >nul 2>&1
        if not errorlevel 1 (
            if not defined PY_EXE set "PY_EXE=%%~i"
        )
    )
)
if defined PY_EXE goto setup_py_path

echo [INFO] Python no esta instalado o no se encuentra en el PATH.
echo [INFO] Preparando instalacion automatica de Python 3.10.11 (estable para Windows 10)...
echo.

echo [PASO 1/3] Buscando instalador de Python local...
if exist "python-3.10.11-amd64.exe" (
    echo [OK] Instalador local python-3.10.11-amd64.exe encontrado.
    set "PYTHON_INSTALLER=python-3.10.11-amd64.exe"
) else if exist "python-3.12.3-amd64.exe" (
    echo [INFO] Instalador local disponible encontrado.
    set "PYTHON_INSTALLER=python-3.12.3-amd64.exe"
) else (
    echo [INFO] Descargando Python 3.10.11 desde python.org (compatible sin administrador)...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; echo 'Descargando Python 3.10...'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe' -OutFile 'python_installer.exe'"
    if errorlevel 1 (
        echo [ERROR] No se pudo descargar el instalador de Python. Asegurate de tener conexion a internet.
        pause
        exit /b 1
    )
    set "PYTHON_INSTALLER=python_installer.exe"
    echo [OK] Descarga completada.
)
echo.

echo [PASO 2/3] Instalando Python sin permisos de administrador...
echo [INFO] Aparecera una ventana mostrando el progreso. Esto tardara aproximadamente 1 minuto...

set "__COMPAT_LAYER=RunAsInvoker"
"%PYTHON_INSTALLER%" /passive InstallAllUsers=0 PrependPath=0 Include_test=0 Include_pip=1 Include_doc=0

if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de Python.
    if "%PYTHON_INSTALLER%"=="python_installer.exe" del python_installer.exe >nul 2>&1
    pause
    exit /b 1
)
if "%PYTHON_INSTALLER%"=="python_installer.exe" del python_installer.exe >nul 2>&1
echo [OK] Instalacion completada con exito.
echo.

echo [PASO 3/3] Configurando variables de entorno temporales...
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" set "PY_EXE=%%~fD\python.exe"
)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
)

:setup_py_path
if defined PY_EXE (
    for %%F in ("%PY_EXE%") do set "PY_DIR=%%~dpF"
    set "PATH=%PY_DIR%;%PY_DIR%Scripts\;%PATH%"
)

"%PY_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se pudo verificar la ejecucion de Python.
    echo Por favor, cierre esta ventana e intente abrir iniciar_servidor.bat de nuevo.
    pause
    exit /b 1
)
echo [OK] Entorno de Python configurado correctamente:
"%PY_EXE%" --version
echo.

:python_installed

rem 2. Crear o reparar entorno virtual si es necesario
set VENV_OK=1
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe --version >nul 2>&1
    if errorlevel 1 set VENV_OK=0
) else (
    set VENV_OK=0
)

if %VENV_OK% equ 0 (
    if exist .venv (
        echo [WARNING] El entorno virtual existente .venv esta danado o proviene de otra PC.
        echo [INFO] Reconstruyendo entorno virtual automaticamente...
        rmdir /s /q .venv >nul 2>&1
    )
    echo [INFO] Creando entorno virtual .venv...
    "%PY_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo [OK] Entorno virtual creado exitosamente.
    echo.
)

:venv_ready

rem 3. Instalar dependencias dentro del entorno virtual
echo [INFO] Instalando/actualizando dependencias de forma silenciosa en el entorno virtual .venv...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [WARNING] Hubo un problema al instalar las dependencias de requirements.txt.
    echo Se intentara continuar...
    echo.
) else (
    echo [OK] Dependencias instaladas exitosamente.
    echo.
)

rem 4. Instalar navegadores de Playwright
echo [INFO] Configurando Playwright para usar navegadores locales...
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0playwright_browsers"

echo [INFO] Instalando navegadores de automatizacion (Playwright). Mostrando progreso...
.venv\Scripts\playwright.exe install chromium
if errorlevel 1 (
    echo [WARNING] No se pudieron instalar los navegadores de Playwright.
    echo Se utilizara Microsoft Edge nativo de Windows 10 como respaldo automatico.
    echo.
) else (
    echo [OK] Navegadores listos.
    echo.
)

rem 5. Crear o reparar archivo .env si no existe o si contiene credenciales de plantilla
set "NEED_ENV_INIT=0"
if not exist .env (
    set "NEED_ENV_INIT=1"
) else (
    findstr /i "pon_tu_usuario_growatt pon_tu_clave_aqui" .env >nul 2>&1
    if not errorlevel 1 set "NEED_ENV_INIT=1"
)

if "%NEED_ENV_INIT%"=="1" (
    if exist .env.example (
        echo [INFO] Configurando archivo .env operativo a partir de .env.example...
        copy /y .env.example .env > nul
        echo. >> .env
        echo # Configuracion de red local >> .env
        echo WEBUI_HOST=0.0.0.0 >> .env
        echo WEBUI_PORT=8000 >> .env
        echo [OK] Archivo .env configurado con credenciales operativas y acceso LAN.
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

rem 6. Verificar si existe una nueva version en GitHub
.venv\Scripts\python.exe -m providers.version_checker >nul 2>&1

rem 7. Iniciar Servidor
if exist version.json (
    for /f "tokens=2 delims=:," %%A in ('findstr "version" version.json') do set "SYS_VER=%%~A"
)
set "SYS_VER=%SYS_VER: =%"
set "SYS_VER=%SYS_VER:"=%"

cls
echo ======================================================================
echo       SISTEMA DE CAPTURA DE INVERSORES (v%SYS_VER%)
echo   Sistema elaborado por Duvelis Huiza y el Lic. Luis G.
echo ======================================================================
echo.
echo    Iniciando Servidor de Interfaz Web...
echo ======================================================================
echo.
.venv\Scripts\python.exe -u webui_server.py
echo.
pause

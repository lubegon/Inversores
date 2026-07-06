@echo off
setlocal EnableDelayedExpansion
title Sistema de Inversores - Tunel Remoto
set "__COMPAT_LAYER=RunAsInvoker"

echo ======================================================================
echo           SISTEMA DE CAPTURA DE INVERSORES - TUNEL WEB
echo   Elaborado por el Lic. Luis G.
echo ======================================================================
echo.
echo Este script creara una conexion segura a traves de Internet usando
echo Cloudflare para que puedas entrar al sistema desde otra PC o Celular
echo sin necesidad de abrir puertos ni usar permisos de Administrador.
echo.

set CLOUDFLARED_EXE=cloudflared.exe
set DOWNLOAD_URL=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe

if not exist "%CLOUDFLARED_EXE%" (
    echo [INFO] 'cloudflared.exe' no encontrado. Descargando cliente oficial...
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile '%CLOUDFLARED_EXE%'"
    if not exist "%CLOUDFLARED_EXE%" (
        echo [ERROR] No se pudo descargar el cliente. Verifique su conexion a Internet.
        pause
        exit /b 1
    )
    echo [INFO] Descarga completada exitosamente.
    echo.
)

echo ======================================================================
echo [INSTRUCCIONES] 
echo Busca en las lineas de abajo un enlace verde parecido a este:
echo "https://palabras-al-azar.trycloudflare.com"
echo. 
echo Copia ese enlace e ingresa desde cualquier dispositivo (Celular o PC).
echo IMPORTANTE: El servidor principal (iniciar_servidor.bat) debe estar corriendo.
echo ======================================================================
echo.

.venv\Scripts\python.exe tunnel_runner.py

pause

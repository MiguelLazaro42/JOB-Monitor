@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title SITAI JOB-Monitor - Desinstalador Modular de Agente (Windows)

set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
set "ALT_INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
set "TASK_BOOT=ServicioMonitoreoCorporativo"
set "TASK_CRON=ServicioMonitoreoCorporativoWatchdog"
set "ORIG_APPDATA=%APPDATA%"
set "IS_ADMIN=0"

REM ============================================================================
REM  ORQUESTADOR DE DESINSTALACION POR MICRO-FUNCIONES (UF-01 a UF-05)
REM ============================================================================

call :uf_01_elevate_privileges "%~1"
if "%ELEVATED_EXIT%"=="1" exit /b 0

echo ================================================================
echo   SITAI JOB-Monitor - Desinstalador Limpio del Agente (Windows)
echo ================================================================
echo.

call :uf_02_remove_scheduled_tasks
call :uf_03_remove_startup_fallbacks
call :uf_04_stop_running_processes
call :uf_05_purge_install_directories

echo.
echo [OK] El agente JOB-Monitor y su Cron de encendido fueron eliminados del equipo.
timeout /t 3 >nul
exit /b 0


REM ============================================================================
REM  UF-01: Verificar privilegios y auto-elevar UAC
REM ============================================================================
:uf_01_elevate_privileges
set "ELEVATED_EXIT=0"
set "CTX_FILE=%PUBLIC%\jm_uninstall_ctx.tmp"
set "ORIG_APPDATA=%APPDATA%"

net session >nul 2>&1
if not errorlevel 1 (
    set "IS_ADMIN=1"
    if "%~1"=="--tried-elevate" if exist "%CTX_FILE%" (
        set /p ORIG_APPDATA=<"%CTX_FILE%"
        del /f /q "%CTX_FILE%" >nul 2>&1
    )
    exit /b 0
)

if not "%~1"=="--tried-elevate" (
    >"%CTX_FILE%" echo(%ORIG_APPDATA%
    set "SELF_BAT=%~f0"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:SELF_BAT -ArgumentList '--tried-elevate' -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
    if not errorlevel 1 (
        set "ELEVATED_EXIT=1"
        exit /b 0
    )
    if exist "%CTX_FILE%" del /f /q "%CTX_FILE%" >nul 2>&1
)
exit /b 0


REM ============================================================================
REM  UF-02: Eliminar tareas programadas (Encendido + Cron cada 1 minuto)
REM ============================================================================
:uf_02_remove_scheduled_tasks
echo [UF-02] Eliminando tareas programadas (%TASK_BOOT% y %TASK_CRON%)...
schtasks /Delete /TN "%TASK_BOOT%" /F >nul 2>&1
schtasks /Delete /TN "%TASK_CRON%" /F >nul 2>&1
exit /b 0


REM ============================================================================
REM  UF-03: Eliminar entradas de Registro Run y accesos en carpeta Startup
REM ============================================================================
:uf_03_remove_startup_fallbacks
echo [UF-03] Limpiando entradas de inicio automatico en Registro y Startup...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_BOOT%" /f >nul 2>&1
reg delete "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_BOOT%" /f >nul 2>&1

for %%S in (
    "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\%TASK_BOOT%.vbs"
    "%ORIG_APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\%TASK_BOOT%.vbs"
    "%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp\%TASK_BOOT%.vbs"
) do (
    if exist "%%~S" del /f /q "%%~S" >nul 2>&1
)
exit /b 0


REM ============================================================================
REM  UF-04: Detener procesos activos asociados al agente
REM ============================================================================
:uf_04_stop_running_processes
echo [UF-04] Deteniendo procesos activos asociados al agente...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe' -or $_.Name -eq 'wscript.exe') -and ($_.CommandLine -like '*agent.py*' -or $_.CommandLine -like '*MonitoreoEmpresa*' -or $_.CommandLine -like '*ServicioMonitoreoCorporativo*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul
exit /b 0


REM ============================================================================
REM  UF-05: Eliminar directorios de instalacion
REM ============================================================================
:uf_05_purge_install_directories
echo [UF-05] Eliminando archivos de instalacion...
if exist "%INSTALL_DIR%" (
    rmdir /s /q "%INSTALL_DIR%" >nul 2>&1
)
if exist "%ALT_INSTALL_DIR%" (
    rmdir /s /q "%ALT_INSTALL_DIR%" >nul 2>&1
)
exit /b 0

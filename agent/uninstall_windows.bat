@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title SITAI JOB-Monitor - Desinstalador de Agente Windows

net session >nul 2>&1
if errorlevel 1 (
    if not "%~1"=="--tried-elevate" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -ArgumentList '--tried-elevate' -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
        if not errorlevel 1 exit /b 0
    )
)

set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
set "ALT_INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
set "TASK_NAME=ServicioMonitoreoCorporativo"

echo ================================================================
echo   SITAI JOB-Monitor - Desinstalador Limpio del Agente (Windows)
echo ================================================================
echo.

echo [1/3] Eliminando tareas programadas (autoinicio + watchdog) y registro...
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1
schtasks /Delete /TN "%TASK_NAME%Watchdog" /F >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_NAME%" /f >nul 2>&1

echo [2/3] Deteniendo procesos activos asociados al agente...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

timeout /t 2 >nul

echo [3/3] Eliminando archivos de instalacion...
if exist "%INSTALL_DIR%" (
    rmdir /s /q "%INSTALL_DIR%" >nul 2>&1
)
if exist "%ALT_INSTALL_DIR%" (
    rmdir /s /q "%ALT_INSTALL_DIR%" >nul 2>&1
)

echo.
echo [OK] El agente JOB-Monitor fue desinstalado completamente del equipo.
timeout /t 3 >nul
exit /b 0

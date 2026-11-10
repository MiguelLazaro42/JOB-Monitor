@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

title Desinstalador de Agente Corporativo JOB-Monitor

set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
set "ALT_INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
set "TASK_NAME=ServicioMonitoreoCorporativo"

echo ================================================================
echo   JOB-Monitor - Desinstalador Limpio del Agente (Fase 4)
echo ================================================================
echo.

echo [1/3] Eliminando tarea programada y registro de autoinicio...
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_NAME%" /f >nul 2>&1

echo [2/3] Deteniendo procesos activos de pythonw.exe asociados al agente...
wmic process where "name='pythonw.exe' and commandline like '%%agent.py%%'" call terminate >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

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

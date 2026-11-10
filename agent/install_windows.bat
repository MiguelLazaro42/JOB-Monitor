@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

title Instalador de Agente Corporativo JOB-Monitor

set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
set "TASK_NAME=ServicioMonitoreoCorporativo"
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE="
set "PYTHONW_EXE="

echo ================================================================
echo   JOB-Monitor - Instalador del Agente Corporativo (Fase 4)
echo ================================================================
echo.

REM ----------------------------------------------------------------
REM Mini-Funcion B1: Detectar python.exe y pythonw.exe oficiales
REM ----------------------------------------------------------------
call :detect_python
if "%PYTHON_EXE%"=="" (
    echo [ERROR] No se encontro una instalacion oficial de Python 3 en este equipo.
    echo Por favor instala Python 3.10+ desde https://www.python.org/downloads/
    echo asegurandote de marcar la casilla "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [OK] Python detectado:  "%PYTHON_EXE%"
echo [OK] PythonW detectado: "%PYTHONW_EXE%"
echo.

REM ----------------------------------------------------------------
REM Mini-Funcion B2: Copiar archivos a C:\ProgramData\MonitoreoEmpresa
REM                  e instalar dependencias ligeras con pip
REM ----------------------------------------------------------------
call :copy_and_install_deps
if errorlevel 1 (
    echo [ERROR] Hubo un problema preparando los archivos o dependencias.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM Mini-Funcion B3: Registrar autoinicio en Windows y arrancar agente
REM ----------------------------------------------------------------
call :register_startup_task

echo.
echo ================================================================
echo   Instalacion completada correctamente.
echo   Carpeta: %INSTALL_DIR%
echo   El agente ya se encuentra en ejecucion con pythonw.exe.
echo ================================================================
timeout /t 4 >nul
exit /b 0


:detect_python
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    echo "%%P" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        if "%PYTHONW_EXE%"=="" set "PYTHONW_EXE=%%P"
    )
)
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    echo "%%P" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%%P"
    )
)

if not "%PYTHON_EXE%"=="" if not "%PYTHONW_EXE%"=="" exit /b 0

for %%D in (
    "%LocalAppData%\Programs\Python\Python313"
    "%LocalAppData%\Programs\Python\Python312"
    "%LocalAppData%\Programs\Python\Python311"
    "%LocalAppData%\Programs\Python\Python310"
    "C:\Program Files\Python313"
    "C:\Program Files\Python312"
    "C:\Program Files\Python311"
    "C:\Program Files\Python310"
) do (
    if exist "%%~D\python.exe" if exist "%%~D\pythonw.exe" (
        set "PYTHON_EXE=%%~D\python.exe"
        set "PYTHONW_EXE=%%~D\pythonw.exe"
        exit /b 0
    )
)
exit /b 0


:copy_and_install_deps
echo [1/3] Preparando directorio %INSTALL_DIR% ...
if not exist "%INSTALL_DIR%" (
    mkdir "%INSTALL_DIR%" 2>nul
)
if not exist "%INSTALL_DIR%" (
    set "INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
    if not exist "!INSTALL_DIR!" mkdir "!INSTALL_DIR!"
)

REM Detener instancia previa si estuviera corriendo para actualizar archivos
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq Soporte*" >nul 2>&1

copy /y "%SCRIPT_DIR%agent.py" "%INSTALL_DIR%\agent.py" >nul
if errorlevel 1 exit /b 1

if exist "%SCRIPT_DIR%config_agent.json" (
    copy /y "%SCRIPT_DIR%config_agent.json" "%INSTALL_DIR%\config_agent.json" >nul
)

copy /y "%SCRIPT_DIR%requirements-agent.txt" "%INSTALL_DIR%\requirements-agent.txt" >nul

echo [2/3] Instalando dependencias de Python (websockets, mss, Pillow, pynput, pyperclip)...
"%PYTHON_EXE%" -m pip install --upgrade pip --quiet >nul 2>&1
"%PYTHON_EXE%" -m pip install -r "%INSTALL_DIR%\requirements-agent.txt" --quiet
if errorlevel 1 (
    "%PYTHON_EXE%" -m pip install --user -r "%INSTALL_DIR%\requirements-agent.txt"
    if errorlevel 1 exit /b 1
)
exit /b 0


:register_startup_task
echo [3/3] Configurando inicio automatico y Watchdog cada 2 minutos en Windows...
schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHONW_EXE%\" \"%INSTALL_DIR%\agent.py\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHONW_EXE%\" \"%INSTALL_DIR%\agent.py\"" /SC ONLOGON /F >nul 2>&1
)

REM Watchdog del Sistema Operativo: verifica cada 2 minutos que el servicio este activo y lo autoarranca si se cerro
schtasks /Create /TN "%TASK_NAME%Watchdog" /TR "\"%PYTHONW_EXE%\" \"%INSTALL_DIR%\agent.py\"" /SC MINUTE /MO 2 /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_NAME%Watchdog" /TR "\"%PYTHONW_EXE%\" \"%INSTALL_DIR%\agent.py\"" /SC MINUTE /MO 2 /F >nul 2>&1
)

REM Respaldo en clave Run del usuario actual por si schtasks esta restringido por politicas locales
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_NAME%" /t REG_SZ /d "\"%PYTHONW_EXE%\" \"%INSTALL_DIR%\agent.py\"" /f >nul 2>&1

echo [OK] Iniciando agente con pythonw.exe...
start "" /d "%INSTALL_DIR%" "%PYTHONW_EXE%" "%INSTALL_DIR%\agent.py"
exit /b 0


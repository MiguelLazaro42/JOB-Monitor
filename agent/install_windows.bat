@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title SITAI JOB-Monitor - Instalador Automatico para Windows

set "SCRIPT_DIR=%~dp0"
set "TASK_NAME=ServicioMonitoreoCorporativo"
set "IS_ADMIN=0"
set "PYTHON_EXE="
set "PYTHONW_EXE="

REM ----------------------------------------------------------------
REM Mini-Funcion W0: Auto-elevacion UAC (con respaldo modo usuario)
REM ----------------------------------------------------------------
net session >nul 2>&1
if not errorlevel 1 (
    set "IS_ADMIN=1"
) else (
    if not "%~1"=="--tried-elevate" (
        echo Solicitando permisos de Administrador...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -ArgumentList '--tried-elevate' -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
        if not errorlevel 1 exit /b 0
        echo [AVISO] Continuando instalacion en modo Usuario Estandar...
    )
)

if "%IS_ADMIN%"=="1" (
    set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
) else (
    set "INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
)

echo ================================================================
echo   SITAI JOB-Monitor - Instalador Todo-en-Uno para Windows
echo ================================================================
echo.

REM ----------------------------------------------------------------
REM Mini-Funcion W1: Detectar Python 3 oficial o instalarlo con PATH
REM ----------------------------------------------------------------
call :detect_python
if "%PYTHON_EXE%"=="" (
    echo [1/4] Python 3 no detectado. Descargando e instalando Python 3.12 oficial con PATH...
    call :auto_install_python
    call :detect_python
)

if "%PYTHON_EXE%"=="" (
    echo [ERROR] No fue posible instalar o localizar Python 3 automaticamente.
    echo Verifica tu conexion a Internet e intenta nuevamente.
    pause
    exit /b 1
)

echo [OK] Python listo : "%PYTHON_EXE%"
echo [OK] PythonW listo: "%PYTHONW_EXE%"
echo.

REM ----------------------------------------------------------------
REM Mini-Funcion W2: Preparar carpeta, entorno virtual y librerias
REM ----------------------------------------------------------------
call :setup_agent_environment
if errorlevel 1 (
    echo [ERROR] Ocurrio un problema preparando las dependencias del agente.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------
REM Mini-Funcion W3: Configurar auto-inicio, Watchdog y arrancar
REM ----------------------------------------------------------------
call :register_startup_task

echo.
echo ================================================================
echo   INSTALACION COMPLETADA EXITOSAMENTE EN WINDOWS
echo   - Directorio : %INSTALL_DIR%
echo   - Auto-inicio: Activo al iniciar sesion + Watchdog cada 2 min
echo   - Estado     : Agente ejecutandose en segundo plano
echo ================================================================
timeout /t 5 >nul
exit /b 0


REM ================================================================
REM  SUBRUTINAS MODULARES
REM ================================================================

:detect_python
set "PYTHON_EXE="
set "PYTHONW_EXE="

for %%D in (
    "C:\Program Files\Python313"
    "C:\Program Files\Python312"
    "C:\Program Files\Python311"
    "C:\Program Files\Python310"
    "%LocalAppData%\Programs\Python\Python313"
    "%LocalAppData%\Programs\Python\Python312"
    "%LocalAppData%\Programs\Python\Python311"
    "%LocalAppData%\Programs\Python\Python310"
    "C:\Program Files (x86)\Python312-32"
    "C:\Program Files (x86)\Python311-32"
) do (
    if exist "%%~D\python.exe" if exist "%%~D\pythonw.exe" (
        "%%~D\python.exe" -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%%~D\python.exe"
            set "PYTHONW_EXE=%%~D\pythonw.exe"
            set "PATH=%%~D;%%~D\Scripts;%PATH%"
            exit /b 0
        )
    )
)

for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    echo "%%P" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        for %%F in ("%%P") do set "PY_FOLDER=%%~dpF"
        if exist "!PY_FOLDER!pythonw.exe" (
            "%%P" -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON_EXE=%%P"
                set "PYTHONW_EXE=!PY_FOLDER!pythonw.exe"
                exit /b 0
            )
        )
    )
)
exit /b 0


:auto_install_python
set "PY_VER=3.12.9"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-arm64.exe"
) else if /i "%PROCESSOR_ARCHITECTURE%"=="x86" (
    if not defined PROCESSOR_ARCHITEW6432 (
        set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%.exe"
    )
)

set "PY_INSTALLER=%TEMP%\python-jobmonitor-setup.exe"
if exist "%PY_INSTALLER%" del /f /q "%PY_INSTALLER%" >nul 2>&1

echo      Descargando instalador oficial desde python.org...
if exist "%SystemRoot%\System32\curl.exe" (
    "%SystemRoot%\System32\curl.exe" -fSL "%PY_URL%" -o "%PY_INSTALLER%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%' -UseBasicParsing"
)

if not exist "%PY_INSTALLER%" (
    echo [ERROR] No se pudo descargar el instalador de Python.
    exit /b 1
)

echo      Instalando Python %PY_VER% y agregando al PATH del sistema (espera ~20s)...
if "%IS_ADMIN%"=="1" (
    "%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_tcltk=1 Include_venv=1 Include_test=0 Shortcuts=0
    if errorlevel 1 (
        "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1 Include_venv=1 Include_test=0 Shortcuts=0
    )
) else (
    "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1 Include_venv=1 Include_test=0 Shortcuts=0
)

del /f /q "%PY_INSTALLER%" >nul 2>&1
exit /b 0


:setup_agent_environment
echo [2/4] Preparando carpeta %INSTALL_DIR% ...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%" 2>nul
if not exist "%INSTALL_DIR%" (
    set "INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
    if not exist "!INSTALL_DIR!" mkdir "!INSTALL_DIR!"
)

REM Detener proceso previo del agente si estaba corriendo para permitir actualizar
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*MonitoreoEmpresa*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

set "OLD_CFG=%INSTALL_DIR%\config_agent.prev.json"
if exist "%INSTALL_DIR%\config_agent.json" (
    copy /y "%INSTALL_DIR%\config_agent.json" "%OLD_CFG%" >nul 2>&1
)

copy /y "%SCRIPT_DIR%agent.py" "%INSTALL_DIR%\agent.py" >nul
if errorlevel 1 exit /b 1
copy /y "%SCRIPT_DIR%requirements-agent.txt" "%INSTALL_DIR%\requirements-agent.txt" >nul

if exist "%SCRIPT_DIR%config_agent.json" (
    copy /y "%SCRIPT_DIR%config_agent.json" "%INSTALL_DIR%\config_agent.json" >nul
)
if exist "%SCRIPT_DIR%agent_client.crt" (
    copy /y "%SCRIPT_DIR%agent_client.crt" "%INSTALL_DIR%\agent_client.crt" >nul
)
if exist "%SCRIPT_DIR%agent_client.key" (
    copy /y "%SCRIPT_DIR%agent_client.key" "%INSTALL_DIR%\agent_client.key" >nul
)

REM Desbloquear marca SmartScreen (Zone.Identifier) de archivos extraidos del ZIP
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%INSTALL_DIR%' -Recurse | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

REM Conservar agent_id previo si se esta reinstalando sobre el mismo equipo
if exist "%OLD_CFG%" if exist "%INSTALL_DIR%\config_agent.json" (
    "%PYTHON_EXE%" -c "import json, pathlib; p1=pathlib.Path(r'%OLD_CFG%'); p2=pathlib.Path(r'%INSTALL_DIR%\config_agent.json'); c1=json.loads(p1.read_text('utf-8')); c2=json.loads(p2.read_text('utf-8')); c2.setdefault('agent_id', c1.get('agent_id','')) if c1.get('agent_id') else None; c2.setdefault('alias', c1.get('alias','')) if c1.get('alias') else None; p2.write_text(json.dumps(c2, indent=2, ensure_ascii=False), 'utf-8'); p1.unlink(missing_ok=True)" >nul 2>&1
)

echo [3/4] Creando entorno aislado e instalando dependencias (websockets, mss, Pillow, pynput)...
set "VENV_DIR=%INSTALL_DIR%\.venv"
if not exist "%VENV_DIR%\Scripts\pythonw.exe" (
    "%PYTHON_EXE%" -m venv "%VENV_DIR%"
)

if exist "%VENV_DIR%\Scripts\python.exe" (
    set "RUN_PY=%VENV_DIR%\Scripts\python.exe"
    set "RUN_PYW=%VENV_DIR%\Scripts\pythonw.exe"
) else (
    set "RUN_PY=%PYTHON_EXE%"
    set "RUN_PYW=%PYTHONW_EXE%"
)

"%RUN_PY%" -m pip install --upgrade pip --quiet >nul 2>&1
"%RUN_PY%" -m pip install -r "%INSTALL_DIR%\requirements-agent.txt" --quiet
if errorlevel 1 (
    "%PYTHON_EXE%" -m pip install --user -r "%INSTALL_DIR%\requirements-agent.txt"
    if errorlevel 1 exit /b 1
    set "RUN_PY=%PYTHON_EXE%"
    set "RUN_PYW=%PYTHONW_EXE%"
)
exit /b 0


:register_startup_task
echo [4/4] Configurando auto-arranque al encender Windows y Watchdog cada 2 minutos...
schtasks /Create /TN "%TASK_NAME%" /TR "\"%RUN_PYW%\" \"%INSTALL_DIR%\agent.py\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_NAME%" /TR "\"%RUN_PYW%\" \"%INSTALL_DIR%\agent.py\"" /SC ONLOGON /F >nul 2>&1
)

schtasks /Create /TN "%TASK_NAME%Watchdog" /TR "\"%RUN_PYW%\" \"%INSTALL_DIR%\agent.py\"" /SC MINUTE /MO 2 /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_NAME%Watchdog" /TR "\"%RUN_PYW%\" \"%INSTALL_DIR%\agent.py\"" /SC MINUTE /MO 2 /F >nul 2>&1
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_NAME%" /t REG_SZ /d "\"%RUN_PYW%\" \"%INSTALL_DIR%\agent.py\"" /f >nul 2>&1

echo [OK] Iniciando agente en segundo plano...
start "" /d "%INSTALL_DIR%" "%RUN_PYW%" "%INSTALL_DIR%\agent.py"
exit /b 0

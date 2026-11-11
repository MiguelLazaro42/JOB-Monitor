@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title SITAI JOB-Monitor - Instalador Modular con Cron de Encendido (Windows)

set "SCRIPT_DIR=%~dp0"
set "TASK_BOOT=ServicioMonitoreoCorporativo"
set "TASK_CRON=ServicioMonitoreoCorporativoWatchdog"
set "IS_ADMIN=0"
set "ORIG_USER="
set "ORIG_APPDATA=%APPDATA%"
set "INSTALL_DIR="
set "PYTHON_EXE="
set "PYTHONW_EXE="
set "RUN_PY="
set "RUN_PYW="
set "VBS_RUNNER="
set "VBS_STARTUP="
set "PS_SCHED="

REM ============================================================================
REM  ORQUESTADOR PRINCIPAL DE MICRO-FUNCIONES (MF-01 a MF-12)
REM ============================================================================

call :mf_01_elevate_privileges "%~1"
if "%ELEVATED_EXIT%"=="1" exit /b 0

echo ====================================================================
echo   SITAI JOB-Monitor - Instalador Modular Todo-en-Uno para Windows
echo ====================================================================
echo.

call :mf_02_init_paths
call :mf_03_detect_python
if "%PYTHON_EXE%"=="" (
    call :mf_04_auto_install_python
    call :mf_03_detect_python
)
if "%PYTHON_EXE%"=="" (
    echo [ERROR] No fue posible instalar o localizar Python 3 automaticamente.
    echo Verifica la conexion a Internet e intenta nuevamente.
    pause
    exit /b 1
)

echo [OK] Motor Python detectado : "%PYTHON_EXE%"
echo.

call :mf_05_stop_previous_agent
call :mf_06_deploy_files_and_certs
if errorlevel 1 (
    echo [ERROR] Fallo la copia de archivos del agente en %INSTALL_DIR%.
    pause
    exit /b 1
)

call :mf_07_build_virtualenv_deps
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias en el entorno virtual.
    pause
    exit /b 1
)

call :mf_08_create_silent_vbs_runner
call :mf_09_write_scheduler_helper
call :mf_10_register_boot_task
call :mf_11_register_cron_watchdog
call :mf_12_register_startup_fallbacks_and_run

echo.
echo ====================================================================
echo   INSTALACION Y CRON DE ENCENDIDO COMPLETADOS EXITOSAMENTE
echo   - Directorio      : %INSTALL_DIR%
echo   - Tarea Encendido : %TASK_BOOT% (Logon + Inicio Rapido + Bateria OK)
echo   - Tarea Cron      : %TASK_CRON% (Verificacion automatica cada 1 min)
echo   - Respaldo Inicio : Carpeta Startup (.vbs) + Registro Run
echo   - Estado Actual   : Agente activo en segundo plano
echo ====================================================================
timeout /t 5 >nul
exit /b 0


REM ============================================================================
REM  MF-01: Verificar privilegios y auto-elevar UAC (preservando usuario real)
REM ============================================================================
:mf_01_elevate_privileges
set "ELEVATED_EXIT=0"
set "CTX_FILE=%PUBLIC%\jm_install_ctx.tmp"
for /f "delims=" %%U in ('whoami 2^>nul') do set "ORIG_USER=%%U"
set "ORIG_APPDATA=%APPDATA%"

net session >nul 2>&1
if not errorlevel 1 (
    set "IS_ADMIN=1"
    if "%~1"=="--tried-elevate" if exist "%CTX_FILE%" (
        set /p ORIG_USER=<"%CTX_FILE%"
        for /f "skip=1 delims=" %%A in ('type "%CTX_FILE%" 2^>nul') do set "ORIG_APPDATA=%%A"
        del /f /q "%CTX_FILE%" >nul 2>&1
    )
    exit /b 0
)

if not "%~1"=="--tried-elevate" (
    echo [MF-01] Solicitando permisos de Administrador para configurar Cron de encendido...
    >"%CTX_FILE%" echo(%ORIG_USER%
    >>"%CTX_FILE%" echo(%ORIG_APPDATA%
    set "SELF_BAT=%~f0"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:SELF_BAT -ArgumentList '--tried-elevate' -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"
    if not errorlevel 1 (
        set "ELEVATED_EXIT=1"
        exit /b 0
    )
    if exist "%CTX_FILE%" del /f /q "%CTX_FILE%" >nul 2>&1
    echo [AVISO] Continuando instalacion en modo Usuario Estandar...
)
exit /b 0


REM ============================================================================
REM  MF-02: Inicializar directorios de instalacion y permisos multi-usuario
REM ============================================================================
:mf_02_init_paths
if "%IS_ADMIN%"=="1" (
    set "INSTALL_DIR=C:\ProgramData\MonitoreoEmpresa"
) else (
    set "INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
)
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%" 2>nul
if not exist "%INSTALL_DIR%" (
    set "INSTALL_DIR=%LocalAppData%\MonitoreoEmpresa"
    if not exist "!INSTALL_DIR!" mkdir "!INSTALL_DIR!"
)
if "%IS_ADMIN%"=="1" (
    icacls "%INSTALL_DIR%" /grant "*S-1-5-32-545:(OI)(CI)M" /T /C /Q >nul 2>&1
)
for /f "delims=" %%S in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$u = ($env:ORIG_USER -split '\\')[-1]; $s = ($u -replace '[^a-zA-Z0-9_-]','_').ToLower().Trim('_'); if ($s) { Write-Output $s } else { Write-Output 'default' }" 2^>nul') do set "SAFE_USER_SLUG=%%S"
if not "%SAFE_USER_SLUG%"=="" if not "%SAFE_USER_SLUG%"=="default" (
    set "TASK_BOOT=ServicioMonitoreoCorporativo_%SAFE_USER_SLUG%"
    set "TASK_CRON=ServicioMonitoreoCorporativoWatchdog_%SAFE_USER_SLUG%"
)
echo [MF-02] Directorio destino preparado: %INSTALL_DIR% (Usuario: %ORIG_USER%)
exit /b 0


REM ============================================================================
REM  MF-03: Detectar instalacion real de Python 3.9+ (ignorando WindowsApps)
REM ============================================================================
:mf_03_detect_python
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


REM ============================================================================
REM  MF-04: Descargar e instalar silenciosamente Python 3.12 oficial con PATH
REM ============================================================================
:mf_04_auto_install_python
echo [MF-04] Python 3 no encontrado. Descargando Python 3.12 oficial con PATH...
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

if exist "%SystemRoot%\System32\curl.exe" (
    "%SystemRoot%\System32\curl.exe" -fSL "%PY_URL%" -o "%PY_INSTALLER%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%' -UseBasicParsing"
)

if not exist "%PY_INSTALLER%" exit /b 1

echo         Instalando Python %PY_VER% y configurando PATH (espera ~20s)...
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


REM ============================================================================
REM  MF-05: Detener instancias previas del agente antes de actualizar
REM ============================================================================
:mf_05_stop_previous_agent
echo [MF-05] Verificando procesos previos del agente en la sesion actual...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$curSess = (Get-Process -Id $PID).SessionId; Get-CimInstance Win32_Process | Where-Object { $_.SessionId -eq $curSess -and ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe' -or $_.Name -eq 'wscript.exe') -and ($_.CommandLine -like '*MonitoreoEmpresa*' -or $_.CommandLine -like '*ServicioMonitoreoCorporativo*') } | ForEach-Object { cmd /c "taskkill /F /T /PID $($_.ProcessId)" >$null 2>&1 }" >nul 2>&1
exit /b 0


REM ============================================================================
REM  MF-06: Copiar codigo, configuracion y certificados + identidad por usuario
REM ============================================================================
:mf_06_deploy_files_and_certs
echo [MF-06] Desplegando archivos y certificados en %INSTALL_DIR%...
del /f /q "%INSTALL_DIR%\config_agent*.json" >nul 2>&1

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

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path $env:INSTALL_DIR -Recurse | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
exit /b 0


REM ============================================================================
REM  MF-07: Construir entorno virtual aislado (.venv) e instalar librerias
REM ============================================================================
:mf_07_build_virtualenv_deps
echo [MF-07] Configurando entorno virtual e instalando dependencias...
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
if "%IS_ADMIN%"=="1" (
    icacls "%INSTALL_DIR%" /grant "*S-1-5-32-545:(OI)(CI)M" /T /C /Q >nul 2>&1
)
exit /b 0


REM ============================================================================
REM  MF-08: Generar lanzadores silenciosos (.vbs UTF-16LE) sin ventana negra
REM ============================================================================
:mf_08_create_silent_vbs_runner
echo [MF-08] Generando lanzadores silenciosos en segundo plano...
set "VBS_RUNNER=%INSTALL_DIR%\run_agent_silent.vbs"
set "VBS_STARTUP=%INSTALL_DIR%\run_agent_startup.vbs"
set "B64_VBS=T3B0aW9uIEV4cGxpY2l0DQpPbiBFcnJvciBSZXN1bWUgTmV4dA0KSWYgV1NjcmlwdC5Bcmd1bWVudHMuQ291bnQgPiAwIFRoZW4NCiAgICBXU2NyaXB0LlNsZWVwIDQwMDANCkVuZCBJZg0KRGltIFdzaFNoZWxsDQpTZXQgV3NoU2hlbGwgPSBDcmVhdGVPYmplY3QoIldTY3JpcHQuU2hlbGwiKQ0KV3NoU2hlbGwuQ3VycmVudERpcmVjdG9yeSA9ICJfX1dPUktfRElSX18iDQpXc2hTaGVsbC5SdW4gIiIiX19QWVdfRVhFX18iIiAiIl9fV09SS19ESVJfX1xhZ2VudC5weSIiIiwgMCwgRmFsc2UNClNldCBXc2hTaGVsbCA9IE5vdGhpbmcNCg=="
set "B64_VBS_ST=T3B0aW9uIEV4cGxpY2l0DQpPbiBFcnJvciBSZXN1bWUgTmV4dA0KV1NjcmlwdC5TbGVlcCA0MDAwDQpEaW0gV3NoU2hlbGwNClNldCBXc2hTaGVsbCA9IENyZWF0ZU9iamVjdCgiV1NjcmlwdC5TaGVsbCIpDQpXc2hTaGVsbC5DdXJyZW50RGlyZWN0b3J5ID0gIl9fV09SS19ESVJfXyINCldzaFNoZWxsLlJ1biAiIiJfX1BZV19FWEVfXyIiICIiX19XT1JLX0RJUl9fXGFnZW50LnB5IiIiLCAwLCBGYWxzZQ0KU2V0IFdzaFNoZWxsID0gTm90aGluZw0K"
"%RUN_PY%" -c "import base64, os, pathlib; d=os.environ['INSTALL_DIR']; p=os.environ['RUN_PYW']; t1=base64.b64decode(os.environ['B64_VBS']).decode('utf-8').replace('__WORK_DIR__', d).replace('__PYW_EXE__', p); t2=base64.b64decode(os.environ['B64_VBS_ST']).decode('utf-8').replace('__WORK_DIR__', d).replace('__PYW_EXE__', p); pathlib.Path(os.environ['VBS_RUNNER']).write_text(t1, encoding='utf-16'); pathlib.Path(os.environ['VBS_STARTUP']).write_text(t2, encoding='utf-16')"
exit /b 0


REM ============================================================================
REM  MF-09: Generar motor PowerShell COM Schedule.Service (Cron nativo Windows)
REM ============================================================================
:mf_09_write_scheduler_helper
set "PS_SCHED=%INSTALL_DIR%\_register_task.ps1"
set "B64_PS_1=cGFyYW0oDQogICAgW3N0cmluZ10kVGFza05hbWUsDQogICAgW3N0cmluZ10kUHl3UGF0aCwNCiAgICBbc3RyaW5nXSRBZ2VudFNjcmlwdCwNCiAgICBbc3RyaW5nXSRXb3JrRGlyLA0KICAgIFtzdHJpbmddJE1vZGUsDQogICAgW2ludF0kSXNBZG1pbiwNCiAgICBbc3RyaW5nXSRUYXJnZXRVc2VyDQopDQokRXJyb3JBY3Rpb25QcmVmZXJlbmNlID0gIlN0b3AiDQoNCiRzZXJ2aWNlID0gTmV3LU9iamVjdCAtQ29tT2JqZWN0ICJTY2hlZHVsZS5TZXJ2aWNlIg0KJHNlcnZpY2UuQ29ubmVjdCgpDQokcm9vdCA9ICRzZXJ2aWNlLkdldEZvbGRlcigiXCIpDQokdGFzayA9ICRzZXJ2aWNlLk5ld1Rhc2soMCkNCg0KJHRhc2suUmVnaXN0cmF0aW9uSW5mby5EZXNjcmlwdGlvbiA9ICJTSVRBSSBKT0ItTW9uaXRvciBDb3Jwb3JhdGUgU2VydmljZSAoJE1vZGUpIg0KJHRhc2suUmVnaXN0cmF0aW9uSW5mby5BdXRob3IgPSAiU0lUQUkiDQoNCiRjdXJJZGVudGl0eSA9IFtTeXN0ZW0uU2VjdXJpdHkuUHJpbmNpcGFsLldpbmRvd3NJZGVudGl0eV06OkdldEN1cnJlbnQoKS5OYW1lDQppZiAoW3N0cmluZ106OklzTnVsbE9yV2hpdGVTcGFjZSgkVGFyZ2V0VXNlcikpIHsNCiAgICAkVGFyZ2V0VXNlciA9ICRjdXJJZGVudGl0eQ0KfSBlbH"
set "B64_PS_2=NlIHsNCiAgICAkVGFyZ2V0VXNlciA9ICRUYXJnZXRVc2VyLlRyaW0oKQ0KfQ0KDQokdGFzay5QcmluY2lwYWwuVXNlcklkID0gJFRhcmdldFVzZXINCiR0YXNrLlByaW5jaXBhbC5Mb2dvblR5cGUgPSAzDQppZiAoJElzQWRtaW4gLWVxIDEpIHsNCiAgICAkdGFzay5QcmluY2lwYWwuUnVuTGV2ZWwgPSAxDQp9IGVsc2Ugew0KICAgICR0YXNrLlByaW5jaXBhbC5SdW5MZXZlbCA9IDANCn0NCg0KJHMgPSAkdGFzay5TZXR0aW5ncw0KJHMuRW5hYmxlZCA9ICR0cnVlDQokcy5IaWRkZW4gPSAkZmFsc2UNCiRzLkRpc2FsbG93U3RhcnRJZk9uQmF0dGVyaWVzID0gJGZhbHNlDQokcy5TdG9wSWZHb2luZ09uQmF0dGVyaWVzID0gJGZhbHNlDQokcy5TdGFydFdoZW5BdmFpbGFibGUgPSAkdHJ1ZQ0KJHMuRXhlY3V0aW9uVGltZUxpbWl0ID0gIlBUMFMiDQokcy5NdWx0aXBsZUluc3RhbmNlcyA9IDINCiRzLkFsbG93RGVtYW5kU3RhcnQgPSAkdHJ1ZQ0KJHMuV2FrZVRvUnVuID0gJGZhbHNlDQoNCmlmICgkTW9kZSAtZXEgImJvb3QiKSB7DQogICAgJHRMb2dvbiA9ICR0YXNrLlRyaWdnZXJzLkNyZWF0ZSg5KQ0KICAgICR0TG9nb24uVXNlcklkID0gJFRhcmdldFVzZXINCiAgICAkdExvZ29uLkVuYWJsZWQgPSAkdHJ1ZQ0KDQogICAg"
set "B64_PS_3=aWYgKCRJc0FkbWluIC1lcSAxKSB7DQogICAgICAgIHRyeSB7DQogICAgICAgICAgICAkdENvbnNvbGUgPSAkdGFzay5UcmlnZ2Vycy5DcmVhdGUoMTEpDQogICAgICAgICAgICAkdENvbnNvbGUuU3RhdGVDaGFuZ2UgPSAxDQogICAgICAgICAgICAkdENvbnNvbGUuVXNlcklkID0gJFRhcmdldFVzZXINCiAgICAgICAgICAgICR0Q29uc29sZS5FbmFibGVkID0gJHRydWUNCg0KICAgICAgICAgICAgJHRVbmxvY2sgPSAkdGFzay5UcmlnZ2Vycy5DcmVhdGUoMTEpDQogICAgICAgICAgICAkdFVubG9jay5TdGF0ZUNoYW5nZSA9IDgNCiAgICAgICAgICAgICR0VW5sb2NrLlVzZXJJZCA9ICRUYXJnZXRVc2VyDQogICAgICAgICAgICAkdFVubG9jay5FbmFibGVkID0gJHRydWUNCiAgICAgICAgfSBjYXRjaCB7fQ0KICAgIH0NCn0gZWxzZSB7DQogICAgJHRUaW1lID0gJHRhc2suVHJpZ2dlcnMuQ3JlYXRlKDEpDQogICAgJHRUaW1lLlN0YXJ0Qm91bmRhcnkgPSAoR2V0LURhdGUpLkFkZFNlY29uZHMoMTUpLlRvU3RyaW5nKCJ5eXl5LU1NLWRkVEhIOm1tOnNzIikNCiAgICAkdFRpbWUuUmVwZXRpdGlvbi5JbnRlcnZhbCA9ICJQVDFNIg0KICAgICR0VGltZS5SZXBldGl0aW9uLlN0b3BBdER1cmF0aW9uRW5kID0gJGZhbHNlDQogIC"
set "B64_PS_4=AgJHRUaW1lLkVuYWJsZWQgPSAkdHJ1ZQ0KDQogICAgJHRMb2dvbkNyb24gPSAkdGFzay5UcmlnZ2Vycy5DcmVhdGUoOSkNCiAgICAkdExvZ29uQ3Jvbi5Vc2VySWQgPSAkVGFyZ2V0VXNlcg0KICAgICR0TG9nb25Dcm9uLlJlcGV0aXRpb24uSW50ZXJ2YWwgPSAiUFQxTSINCiAgICAkdExvZ29uQ3Jvbi5SZXBldGl0aW9uLlN0b3BBdER1cmF0aW9uRW5kID0gJGZhbHNlDQogICAgJHRMb2dvbkNyb24uRW5hYmxlZCA9ICR0cnVlDQp9DQoNCiRhY3Rpb24gPSAkdGFzay5BY3Rpb25zLkNyZWF0ZSgwKQ0KJGFjdGlvbi5QYXRoID0gJFB5d1BhdGgNCiRhY3Rpb24uQXJndW1lbnRzID0gImAiJEFnZW50U2NyaXB0YCIiDQokYWN0aW9uLldvcmtpbmdEaXJlY3RvcnkgPSAkV29ya0Rpcg0KDQp0cnkgew0KICAgICRyb290LlJlZ2lzdGVyVGFza0RlZmluaXRpb24oJFRhc2tOYW1lLCAkdGFzaywgNiwgJFRhcmdldFVzZXIsICRudWxsLCAzKSB8IE91dC1OdWxsDQp9IGNhdGNoIHsNCiAgICAkdGFzay5QcmluY2lwYWwuUnVuTGV2ZWwgPSAwDQogICAgdHJ5IHsNCiAgICAgICAgJHJvb3QuUmVnaXN0ZXJUYXNrRGVmaW5pdGlvbigkVGFza05hbWUsICR0YXNrLCA2LCAkVGFyZ2V0VXNlciwgJG51bGwsIDMpIHwgT3V0LU51bGwNCiAgICB9"
set "B64_PS_5=IGNhdGNoIHsNCiAgICAgICAgJHRhc2suUHJpbmNpcGFsLlVzZXJJZCA9ICRjdXJJZGVudGl0eQ0KICAgICAgICBpZiAoJElzQWRtaW4gLWVxIDEpIHsgJHRhc2suUHJpbmNpcGFsLlJ1bkxldmVsID0gMSB9DQogICAgICAgIGZvcmVhY2ggKCR0ciBpbiAkdGFzay5UcmlnZ2Vycykgew0KICAgICAgICAgICAgdHJ5IHsgJHRyLlVzZXJJZCA9ICRjdXJJZGVudGl0eSB9IGNhdGNoIHt9DQogICAgICAgIH0NCiAgICAgICAgdHJ5IHsNCiAgICAgICAgICAgICRyb290LlJlZ2lzdGVyVGFza0RlZmluaXRpb24oJFRhc2tOYW1lLCAkdGFzaywgNiwgJGN1cklkZW50aXR5LCAkbnVsbCwgMykgfCBPdXQtTnVsbA0KICAgICAgICB9IGNhdGNoIHsNCiAgICAgICAgICAgICR0YXNrLlByaW5jaXBhbC5SdW5MZXZlbCA9IDANCiAgICAgICAgICAgICRyb290LlJlZ2lzdGVyVGFza0RlZmluaXRpb24oJFRhc2tOYW1lLCAkdGFzaywgNiwgJGN1cklkZW50aXR5LCAkbnVsbCwgMykgfCBPdXQtTnVsbA0KICAgICAgICB9DQogICAgfQ0KfQ0K"
"%RUN_PY%" -c "import base64, os, pathlib; raw=''.join(os.environ['B64_PS_'+str(i)] for i in range(1,6)); pathlib.Path(os.environ['PS_SCHED']).write_text(base64.b64decode(raw).decode('utf-8'), encoding='utf-8-sig')"
exit /b 0


REM ============================================================================
REM  MF-10: Registrar Tarea de Inicializacion al Encender Equipo (Boot/Logon)
REM ============================================================================
:mf_10_register_boot_task
echo [MF-10] Registrando inicializacion al encender el equipo (%TASK_BOOT%)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCHED%" -TaskName "%TASK_BOOT%" -PywPath "%RUN_PYW%" -AgentScript "%INSTALL_DIR%\agent.py" -WorkDir "%INSTALL_DIR%" -Mode "boot" -IsAdmin %IS_ADMIN% -TargetUser "%ORIG_USER%" >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_BOOT%" /TR "wscript.exe //B //NoLogo \"%VBS_RUNNER%\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
    if errorlevel 1 (
        schtasks /Create /TN "%TASK_BOOT%" /TR "wscript.exe //B //NoLogo \"%VBS_RUNNER%\"" /SC ONLOGON /F >nul 2>&1
    )
)
exit /b 0


REM ============================================================================
REM  MF-11: Registrar Cron de Windows cada 1 minuto (Auto-recuperacion continua)
REM ============================================================================
:mf_11_register_cron_watchdog
echo [MF-11] Registrando Cron de Windows cada 1 minuto (%TASK_CRON%)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCHED%" -TaskName "%TASK_CRON%" -PywPath "%RUN_PYW%" -AgentScript "%INSTALL_DIR%\agent.py" -WorkDir "%INSTALL_DIR%" -Mode "cron" -IsAdmin %IS_ADMIN% -TargetUser "%ORIG_USER%" >nul 2>&1
if errorlevel 1 (
    schtasks /Create /TN "%TASK_CRON%" /TR "wscript.exe //B //NoLogo \"%VBS_RUNNER%\"" /SC MINUTE /MO 1 /RL HIGHEST /F >nul 2>&1
    if errorlevel 1 (
        schtasks /Create /TN "%TASK_CRON%" /TR "wscript.exe //B //NoLogo \"%VBS_RUNNER%\"" /SC MINUTE /MO 1 /F >nul 2>&1
    )
)
if exist "%PS_SCHED%" del /f /q "%PS_SCHED%" >nul 2>&1
exit /b 0


REM ============================================================================
REM  MF-12: Registrar respaldos de arranque (Startup + Registro) e iniciar ahora
REM ============================================================================
:mf_12_register_startup_fallbacks_and_run
echo [MF-12] Activando respaldos de inicio y arrancando agente ahora...

set "USER_STARTUP=%ORIG_APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%USER_STARTUP%" (
    copy /y "%VBS_STARTUP%" "%USER_STARTUP%\%TASK_BOOT%.vbs" >nul 2>&1
)

if "%IS_ADMIN%"=="1" (
    set "COMMON_STARTUP=%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp"
    if exist "!COMMON_STARTUP!" (
        copy /y "%VBS_STARTUP%" "!COMMON_STARTUP!\%TASK_BOOT%.vbs" >nul 2>&1
    )
    reg add "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_BOOT%" /t REG_SZ /d "wscript.exe //B //NoLogo \"%VBS_RUNNER%\" --delay" /f >nul 2>&1
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASK_BOOT%" /t REG_SZ /d "wscript.exe //B //NoLogo \"%VBS_RUNNER%\" --delay" /f >nul 2>&1

schtasks /Run /TN "%TASK_BOOT%" >nul 2>&1
timeout /t 2 /nobreak >nul
start "" /d "%INSTALL_DIR%" wscript.exe //B //NoLogo "%VBS_RUNNER%"
exit /b 0

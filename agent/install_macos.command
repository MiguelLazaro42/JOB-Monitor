#!/usr/bin/env bash
# ==============================================================================
#  SITAI JOB-Monitor — Instalador Universal Autonomo para macOS (Intel & M1-M4)
#  - No requiere Xcode Command Line Tools ni Python preinstalado (usa uv 3.12)
#  - Instala aplicacion nativa Mach-O en /Applications/JOB-Monitor Agent.app
#  - Limpia hashes TCC obsoletos si existia una version previa instalada
#  - Registra LaunchAgent permanente al encender el Mac y permisos TCC reales
# ==============================================================================
set -e

cd "$(dirname "$0")"
SRC_DIR="$(pwd)"
xattr -cr "$SRC_DIR" 2>/dev/null || true

INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
STATUS_JSON="$INSTALL_DIR/permissions_status.json"
RUNTIME_JSON="$INSTALL_DIR/runtime_paths.json"
CURRENT_UID=$(id -u)
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
AGENT_ACTIVATED=0

if [ -w "/Applications" ]; then
    APPS_DIR="/Applications"
    if [ -d "$HOME/Applications/JOB-Monitor Agent.app" ]; then
        "$LSREG" -u "$HOME/Applications/JOB-Monitor Agent.app" >/dev/null 2>&1 || true
        rm -rf "$HOME/Applications/JOB-Monitor Agent.app" 2>/dev/null || true
    fi
else
    APPS_DIR="$HOME/Applications"
    mkdir -p "$APPS_DIR"
fi

APP_BUNDLE="$APPS_DIR/JOB-Monitor Agent.app"
APP_MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
APP_EXEC="$APP_MACOS_DIR/JOBMonitorAgent"

# Micro-funcion M1: Consultar permisos reales del .app via LaunchServices
# (NUNCA ejecutar $APP_EXEC directo desde bash, porque macOS tccd evalua a Terminal.app como proceso responsable)
probe_app_permissions() {
    rm -f "$STATUS_JSON"
    open -n -a "$APP_BUNDLE" --args --check-permissions >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8; do
        if [ -f "$STATUS_JSON" ]; then
            break
        fi
        sleep 0.2
    done
    SCREEN_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('screen') else '0')" 2>/dev/null || echo "0")
    AX_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('accessibility') else '0')" 2>/dev/null || echo "0")
    FDA_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('full_disk') else '0')" 2>/dev/null || echo "0")
}

# Micro-funcion M2: Registrar el .app en TCC (Grabacion de Pantalla + Accesibilidad + Audio + Acceso Total al Disco)
request_app_permissions() {
    rm -f "$STATUS_JSON"
    open -n -a "$APP_BUNDLE" --args --request-permissions >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if [ -f "$STATUS_JSON" ]; then
            break
        fi
        sleep 0.4
    done
    SCREEN_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('screen') else '0')" 2>/dev/null || echo "0")
    AX_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('accessibility') else '0')" 2>/dev/null || echo "0")
    FDA_OK=$("$VENV_PY" -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('full_disk') else '0')" 2>/dev/null || echo "0")
}

# Micro-funcion M3: Activar LaunchAgent de forma garantizada incluso si el usuario cierra con Ctrl+C
activate_launchagent() {
    if [ "$AGENT_ACTIVATED" = "1" ] || [ ! -f "$PLIST_PATH" ]; then
        return 0
    fi
    AGENT_ACTIVATED=1
    launchctl bootstrap "gui/$CURRENT_UID" "$PLIST_PATH" >/dev/null 2>&1 || launchctl load -w "$PLIST_PATH" >/dev/null 2>&1 || true
    launchctl enable "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
}

echo "===================================================================="
echo "  SITAI JOB-Monitor — Instalador Nativo para macOS (Intel y Apple M)"
echo "===================================================================="
echo ""

# Detener instancia previa del servicio antes de actualizar scripts
launchctl bootout "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
pkill -f "JOBMonitorAgent" >/dev/null 2>&1 || true

echo "[1/5] Preparando archivos del agente en $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/bin" "$PLIST_DIR"

rm -f "$INSTALL_DIR"/config_agent*.json 2>/dev/null || true

cp -f "$SRC_DIR/agent.py" "$INSTALL_DIR/agent.py"
cp -f "$SRC_DIR/requirements-agent.txt" "$INSTALL_DIR/requirements-agent.txt"
if [ -f "$SRC_DIR/config_agent.json" ]; then
    cp -f "$SRC_DIR/config_agent.json" "$INSTALL_DIR/config_agent.json"
fi
if [ -f "$SRC_DIR/agent_client.crt" ]; then
    cp -f "$SRC_DIR/agent_client.crt" "$INSTALL_DIR/agent_client.crt"
fi
if [ -f "$SRC_DIR/agent_client.key" ]; then
    cp -f "$SRC_DIR/agent_client.key" "$INSTALL_DIR/agent_client.key"
    chmod 600 "$INSTALL_DIR/agent_client.key"
fi
xattr -cr "$INSTALL_DIR" 2>/dev/null || true

echo "[2/5] Configurando motor Python 3.12 autonomo y dependencias..."
UV_BIN="$INSTALL_DIR/bin/uv"
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    UV_URL="https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz"
else
    UV_URL="https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-apple-darwin.tar.gz"
fi

VENV_READY=0
if [ ! -x "$UV_BIN" ]; then
    curl -fsSL "$UV_URL" | tar -xz -C "$INSTALL_DIR/bin" --strip-components=1 2>/dev/null || true
    chmod 755 "$UV_BIN" 2>/dev/null || true
fi

if [ -x "$UV_BIN" ]; then
    export UV_PYTHON_INSTALL_DIR="$INSTALL_DIR/python_runtime"
    if "$UV_BIN" venv --clear --python 3.12 "$INSTALL_DIR/.venv" >/dev/null 2>&1; then
        "$UV_BIN" pip install --python "$INSTALL_DIR/.venv/bin/python" -r "$INSTALL_DIR/requirements-agent.txt" -q
        VENV_READY=1
    fi
fi

if [ "$VENV_READY" -eq 0 ]; then
    SYS_PY=""
    for cand in \
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3"; do
        if [ -x "$cand" ]; then
            SYS_PY="$cand"
            break
        fi
    done
    if [ -z "$SYS_PY" ] && xcode-select -p >/dev/null 2>&1 && [ -x "/usr/bin/python3" ]; then
        SYS_PY="/usr/bin/python3"
    fi
    if [ -z "$SYS_PY" ]; then
        echo "[ERROR] No se pudo aprovisionar Python 3 automaticamente en este Mac."
        exit 1
    fi
    "$SYS_PY" -m venv "$INSTALL_DIR/.venv"
    "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
    "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements-agent.txt" -q
fi

VENV_PY="$INSTALL_DIR/.venv/bin/python"

"$VENV_PY" - "$INSTALL_DIR" "$RUNTIME_JSON" <<'PYRT'
import json, pathlib, site, sys, sysconfig
install_dir = pathlib.Path(sys.argv[1]).resolve()
out_json = pathlib.Path(sys.argv[2])
base_p = pathlib.Path(sys.base_prefix)
ld = sysconfig.get_config_var("LDLIBRARY") or ""
cands = [
    base_p / "lib" / f"libpython{sys.version_info.major}.{sys.version_info.minor}.dylib",
    base_p / "Python3",
    base_p / "Python",
    base_p / ld if ld else base_p / "_none_",
]
dylib = next((str(x.resolve()) for x in cands if x.exists()), "")
payload = {
    "install_dir": str(install_dir),
    "python_home": str(base_p.resolve()),
    "python_dylib": dylib,
    "python_bin": str((install_dir / ".venv" / "bin" / "python").resolve()),
    "site_packages": site.getsitepackages()[0],
}
out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PYRT

echo "[3/5] Verificando aplicacion nativa en '$APP_BUNDLE'..."
if [ ! -f "$SRC_DIR/JOBMonitorAgent_macos" ]; then
    echo "[ERROR] No se encontro el binario nativo JOBMonitorAgent_macos en el paquete."
    exit 1
fi

TMP_PLIST="$INSTALL_DIR/Info.plist.expected"
cat > "$TMP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>$PLIST_LABEL</string>
    <key>CFBundleName</key>
    <string>JOB-Monitor Agent</string>
    <key>CFBundleDisplayName</key>
    <string>JOB-Monitor Agent</string>
    <key>CFBundleExecutable</key>
    <string>JOBMonitorAgent</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.4</string>
    <key>CFBundleVersion</key>
    <string>2.4.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSScreenCaptureUsageDescription</key>
    <string>JOB-Monitor requiere permiso de Grabacion de Pantalla y Audio del Sistema para transmitir imagen en tiempo real sin pantalla gris.</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>JOB-Monitor utiliza funciones de audio para soporte y asistencia remota.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>JOB-Monitor supervisa la actividad de aplicaciones y ventanas del equipo corporativo.</string>
    <key>NSDesktopFolderUsageDescription</key>
    <string>JOB-Monitor permite soporte y gestion remota de archivos en el Escritorio.</string>
    <key>NSDocumentsFolderUsageDescription</key>
    <string>JOB-Monitor permite soporte y gestion remota de archivos en Documentos.</string>
    <key>NSDownloadsFolderUsageDescription</key>
    <string>JOB-Monitor permite soporte y gestion remota de archivos en Descargas.</string>
    <key>NSRemovableVolumesUsageDescription</key>
    <string>JOB-Monitor permite soporte remoto sobre unidades externas conectadas.</string>
    <key>NSNetworkVolumesUsageDescription</key>
    <string>JOB-Monitor permite soporte remoto sobre unidades de red conectadas.</string>
</dict>
</plist>
EOF

NEED_REBUILD_APP=1
if [ -x "$APP_EXEC" ] && [ -f "$APP_BUNDLE/Contents/Info.plist" ]; then
    if cmp -s "$SRC_DIR/JOBMonitorAgent_macos" "$APP_EXEC" && cmp -s "$TMP_PLIST" "$APP_BUNDLE/Contents/Info.plist"; then
        NEED_REBUILD_APP=0
    fi
fi

if [ "$NEED_REBUILD_APP" = "1" ]; then
    if [ -d "$APP_BUNDLE" ]; then
        "$LSREG" -u "$APP_BUNDLE" >/dev/null 2>&1 || true
        rm -rf "$APP_BUNDLE"
    fi
    mkdir -p "$APP_MACOS_DIR" "$APP_BUNDLE/Contents/Resources"
    cp -f "$TMP_PLIST" "$APP_BUNDLE/Contents/Info.plist"
    if [ -f "$SRC_DIR/AppIcon.icns" ]; then
        cp -f "$SRC_DIR/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
    fi
    cp -f "$SRC_DIR/JOBMonitorAgent_macos" "$APP_EXEC"
    chmod 755 "$APP_EXEC"
    xattr -cr "$APP_BUNDLE" 2>/dev/null || true
    codesign --force --deep --sign - --identifier "$PLIST_LABEL" "$APP_BUNDLE" >/dev/null 2>&1 || true
    "$LSREG" -f -R -trusted "$APP_BUNDLE" >/dev/null 2>&1 || "$LSREG" -f "$APP_BUNDLE" >/dev/null 2>&1 || true
fi
rm -f "$TMP_PLIST" 2>/dev/null || true

# Preparar LaunchAgent y trampa EXIT para garantizar que el servicio SIEMPRE quede activo
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>AssociatedBundleIdentifiers</key>
    <array>
        <string>$PLIST_LABEL</string>
    </array>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_EXEC</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/agent.err.log</string>
</dict>
</plist>
EOF
trap 'activate_launchagent' EXIT INT TERM

echo "[4/5] Verificando y registrando permisos nativos en Privacidad y Seguridad..."
SCREEN_OK="0"
AX_OK="0"
FDA_OK="0"
probe_app_permissions

# Si habia una version previa con CDHash distinto, limpiar el registro obsoleto de TCC antes de registrar el nuevo
if [ "$SCREEN_OK" != "1" ]; then
    tccutil reset ScreenCapture "$PLIST_LABEL" >/dev/null 2>&1 || true
fi
if [ "$AX_OK" != "1" ]; then
    tccutil reset Accessibility "$PLIST_LABEL" >/dev/null 2>&1 || true
fi
if [ "$FDA_OK" != "1" ]; then
    tccutil reset SystemPolicyAllFiles "$PLIST_LABEL" >/dev/null 2>&1 || true
fi

if [ "$SCREEN_OK" != "1" ] || [ "$AX_OK" != "1" ] || [ "$FDA_OK" != "1" ]; then
    request_app_permissions
fi

if [ "$SCREEN_OK" != "1" ] || [ "$AX_OK" != "1" ] || [ "$FDA_OK" != "1" ]; then
    echo ""
    echo "===================================================================="
    echo "  AUTORIZACION DE PERMISOS REQUERIDA EN ESTE MAC"
    echo "===================================================================="
    OPENED_AX_PANE=0
    OPENED_FDA_PANE=0

    if [ "$SCREEN_OK" != "1" ]; then
        echo "  -> [1] Grabacion de pantalla y audio del sistema : PENDIENTE"
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" >/dev/null 2>&1 || true
    else
        echo "  -> [1] Grabacion de pantalla y audio del sistema : CONCEDIDO"
    fi
    if [ "$AX_OK" != "1" ]; then
        echo "  -> [2] Accesibilidad (Teclado y Control Remoto)  : PENDIENTE"
        if [ "$SCREEN_OK" = "1" ]; then
            OPENED_AX_PANE=1
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
        fi
    else
        echo "  -> [2] Accesibilidad (Teclado y Control Remoto)  : CONCEDIDO"
    fi
    if [ "$FDA_OK" != "1" ]; then
        echo "  -> [3] Acceso total al disco (Archivos sin avisos): PENDIENTE"
        if [ "$SCREEN_OK" = "1" ] && [ "$AX_OK" = "1" ]; then
            OPENED_FDA_PANE=1
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" >/dev/null 2>&1 || true
            open -R "$APP_BUNDLE" >/dev/null 2>&1 || true
        fi
    else
        echo "  -> [3] Acceso total al disco (Archivos sin avisos): CONCEDIDO"
    fi

    osascript -e 'display dialog "Activa los 3 interruptores de \"JOB-Monitor Agent\" en Ajustes del Sistema:\n\n1. Grabación de pantalla y audio del sistema\n2. Accesibilidad\n3. Acceso total al disco (para navegar carpetas sin avisos repetitivos)\n\nNota: Si en Acceso total al disco aún no aparece en la lista, arrastra \"JOB-Monitor Agent\" desde la ventana de Finder hacia Ajustes." with title "SITAI JOB-Monitor — Permisos Requeridos" buttons {"Entendido"} default button 1 with icon caution giving up after 25' >/dev/null 2>&1 &

    echo ""
    echo "  Esperando a que actives los interruptores de 'JOB-Monitor Agent'..."
    for _ in $(seq 1 60); do
        probe_app_permissions

        if [ "$SCREEN_OK" = "1" ] && [ "$AX_OK" = "1" ] && [ "$FDA_OK" = "1" ]; then
            echo "  [OK] Los 3 permisos (Pantalla, Accesibilidad y Acceso Total al Disco) estan ACTIVOS."
            break
        fi
        if [ "$SCREEN_OK" = "1" ] && [ "$AX_OK" != "1" ] && [ "$OPENED_AX_PANE" = "0" ]; then
            OPENED_AX_PANE=1
            echo "  [OK] Grabacion de Pantalla activada. Abriendo panel de Accesibilidad..."
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
        fi
        if [ "$SCREEN_OK" = "1" ] && [ "$AX_OK" = "1" ] && [ "$FDA_OK" != "1" ] && [ "$OPENED_FDA_PANE" = "0" ]; then
            OPENED_FDA_PANE=1
            echo "  [OK] Accesibilidad activada. Abriendo panel de Acceso Total al Disco..."
            echo "       (Si no aparece en la lista, arrastra 'JOB-Monitor Agent' desde Finder hacia Ajustes)"
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" >/dev/null 2>&1 || true
            open -R "$APP_BUNDLE" >/dev/null 2>&1 || true
        fi
        sleep 2
    done
fi

echo "[5/5] Activando auto-arranque al encender el Mac y conectando al servidor..."
activate_launchagent

echo ""
echo "===================================================================="
echo "  AGENTE CONECTADO Y AUTO-INICIO CONFIGURADO EXITOSAMENTE"
echo "  - Ruta de la App  : $APP_BUNDLE"
echo "  - Auto-inicio Mac : Activo al encender/iniciar sesion (KeepAlive)"
if [ "$SCREEN_OK" = "1" ] && [ "$AX_OK" = "1" ] && [ "$FDA_OK" = "1" ]; then
    echo "  - Permisos macOS  : Pantalla, Accesibilidad y Acceso Total ACTIVOS"
else
    echo "  - Permisos macOS  : En cuanto actives los interruptores, se aplicaran al instante"
fi
echo "===================================================================="
echo ""

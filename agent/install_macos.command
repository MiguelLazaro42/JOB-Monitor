#!/usr/bin/env bash
# ==============================================================================
#  JOB-Monitor — Instalador de Agente Corporativo + Watchdog para macOS
#  Instala en ~/.local/share/MonitoreoEmpresa y registra LaunchAgent KeepAlive
# ==============================================================================
set -e

# Quitar cuarentena de macOS (Gatekeeper) en la carpeta del instalador
xattr -dr com.apple.quarantine "$(dirname "$0")" 2>/dev/null || true
cd "$(dirname "$0")"

SRC_DIR="$(pwd)"
INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
CURRENT_UID=$(id -u)

echo "===================================================================="
echo "  Instalador de Agente de Monitoreo y Asistencia Remota (macOS)"
echo "===================================================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] No se encontro Python 3 en este Mac."
    echo "Instala Python 3 desde https://www.python.org/downloads/macos/ o Xcode Command Line Tools."
    read -r -p "Presiona Enter para salir..."
    exit 1
fi

echo "[1/5] Preparando directorio en $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$PLIST_DIR"

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

echo "[2/5] Configurando entorno virtual e instalando dependencias..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements-agent.txt" -q

echo "[3/5] Registrando servicio de auto-arranque continuo (LaunchAgent + KeepAlive)..."
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/.venv/bin/python</string>
        <string>$INSTALL_DIR/agent.py</string>
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

echo "[4/5] Solicitando permisos de Grabacion de Pantalla y Accesibilidad en macOS..."
"$INSTALL_DIR/.venv/bin/python" - <<'PYCHECK'
import ctypes, subprocess

print("Comprobando autorizaciones del sistema en macOS...")

# 1. Grabacion de pantalla (Screen Recording) - Indispensable para video
try:
    cg = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    screen_ok = False
    if hasattr(cg, 'CGPreflightScreenCaptureAccess'):
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        screen_ok = cg.CGPreflightScreenCaptureAccess()

    if not screen_ok:
        print("\n--------------------------------------------------------------------")
        print(" [ACCION REQUERIDA] PERMISO DE GRABACION DE PANTALLA")
        print(" Para poder transmitir video, macOS requiere autorizar la grabacion.")
        print("--------------------------------------------------------------------")
        if hasattr(cg, 'CGRequestScreenCaptureAccess'):
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            cg.CGRequestScreenCaptureAccess()
        
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Se ha abierto Ajustes del Sistema -> Privacidad y Seguridad -> Grabacion de pantalla.")
        print(" Activa el interruptor para 'Python' o 'Terminal'.")
    else:
        print(" [OK] Permiso de Grabacion de Pantalla: CONCEDIDO")
except Exception as e:
    print(f" Aviso en verificacion de pantalla: {e}")

# 2. Accesibilidad (Para control remoto de mouse y teclado)
try:
    app_serv = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    ax_ok = False
    if hasattr(app_serv, 'AXIsProcessTrusted'):
        app_serv.AXIsProcessTrusted.restype = ctypes.c_bool
        ax_ok = app_serv.AXIsProcessTrusted()

    if not ax_ok:
        print("\n--------------------------------------------------------------------")
        print(" [ACCION REQUERIDA] PERMISO DE ACCESIBILIDAD (Para Control Remoto)")
        print(" Si deseas permitir control de mouse y teclado remoto:")
        print("--------------------------------------------------------------------")
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Se ha abierto Ajustes del Sistema -> Privacidad y Seguridad -> Accesibilidad.")
        print(" Activa el interruptor para 'Python' o 'Terminal'.")
    else:
        print(" [OK] Permiso de Accesibilidad (Control Remoto): CONCEDIDO")
except Exception as e:
    print(f" Aviso en verificacion de accesibilidad: {e}")
PYCHECK

echo "[5/5] Activando e iniciando el servicio en segundo plano..."
launchctl bootout "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$CURRENT_UID" "$PLIST_PATH" >/dev/null 2>&1 || launchctl load -w "$PLIST_PATH"
launchctl enable "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true

echo ""
echo "===================================================================="
echo "  INSTALACION COMPLETADA EXITOSAMENTE EN MACOS"
echo "  - Directorio : $INSTALL_DIR"
echo "  - Watchdog   : LaunchAgent ($PLIST_LABEL con KeepAlive=true)"
echo "  - Auto-inicio: Activo en cada arranque y reinicio del Mac"
echo "===================================================================="
echo ""
read -r -t 15 -p "Presiona Enter para cerrar esta ventana (o se cerrara sola en 15s)..." || true


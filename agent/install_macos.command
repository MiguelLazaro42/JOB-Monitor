#!/usr/bin/env bash
# ==============================================================================
#  JOB-Monitor — Instalador de Agente Corporativo + Watchdog para macOS
#  Instala en ~/.local/share/MonitoreoEmpresa y registra LaunchAgent KeepAlive
# ==============================================================================
set -e

cd "$(dirname "$0")"
SRC_DIR="$(pwd)"
INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"

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

echo "[1/4] Preparando directorio en $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$PLIST_DIR"

cp -f "$SRC_DIR/agent.py" "$INSTALL_DIR/agent.py"
cp -f "$SRC_DIR/requirements-agent.txt" "$INSTALL_DIR/requirements-agent.txt"
if [ -f "$SRC_DIR/config_agent.json" ]; then
    cp -f "$SRC_DIR/config_agent.json" "$INSTALL_DIR/config_agent.json"
fi

echo "[2/4] Configurando entorno virtual e instalando dependencias..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements-agent.txt" -q

echo "[3/4] Registrando servicio auto-arranque + Watchdog nativo (launchd KeepAlive)..."
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

echo "[4/4] Iniciando el agente en segundo plano..."
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load -w "$PLIST_PATH"

echo ""
echo "===================================================================="
echo "  INSTALACION COMPLETADA EXITOSAMENTE EN MACOS"
echo "  - Directorio : $INSTALL_DIR"
echo "  - Watchdog   : LaunchAgent ($PLIST_LABEL con KeepAlive=true)"
echo "  - Nota macOS : Si macOS solicita permisos de Grabacion de Pantalla"
echo "                 o Accesibilidad para Python, aceptalos en"
echo "                 Configuracion del Sistema -> Privacidad y Seguridad."
echo "===================================================================="
echo ""

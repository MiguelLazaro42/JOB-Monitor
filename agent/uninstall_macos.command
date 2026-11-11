#!/usr/bin/env bash
# ==============================================================================
#  SITAI JOB-Monitor — Desinstalador de Agente Corporativo para macOS
# ==============================================================================
set -e

xattr -cr "$(dirname "$0")" 2>/dev/null || true
cd "$(dirname "$0")"

INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
CURRENT_UID=$(id -u)
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

echo "===================================================================="
echo "  Desinstalador de Agente de Monitoreo Corporativo (macOS)"
echo "===================================================================="
echo ""

echo "[1/4] Deteniendo LaunchAgent y Watchdog..."
launchctl bootout "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
if [ -f "$PLIST_PATH" ]; then
    launchctl unload -w "$PLIST_PATH" >/dev/null 2>&1 || true
    rm -f "$PLIST_PATH"
fi

echo "[2/4] Cerrando procesos activos del agente..."
pkill -f "JOBMonitorAgent" >/dev/null 2>&1 || true
pkill -f "MonitoreoEmpresa/agent.py" >/dev/null 2>&1 || true

echo "[3/4] Eliminando aplicacion 'JOB-Monitor Agent.app' y datos locales..."
for app_path in "/Applications/JOB-Monitor Agent.app" "$HOME/Applications/JOB-Monitor Agent.app"; do
    if [ -d "$app_path" ]; then
        "$LSREG" -u "$app_path" >/dev/null 2>&1 || true
        rm -rf "$app_path" 2>/dev/null || true
    fi
done
rm -rf "$INSTALL_DIR"
find /tmp -maxdepth 1 \( -name "*jobmonitor*" -o -name "*job_monitor*" \) -exec rm -rf {} + 2>/dev/null || true

echo "[4/4] Limpiando registros previos en Privacidad y Seguridad (TCC)..."
tccutil reset ScreenCapture "$PLIST_LABEL" >/dev/null 2>&1 || true
tccutil reset Accessibility "$PLIST_LABEL" >/dev/null 2>&1 || true
tccutil reset Microphone "$PLIST_LABEL" >/dev/null 2>&1 || true
tccutil reset SystemPolicyAllFiles "$PLIST_LABEL" >/dev/null 2>&1 || true
tccutil reset All "$PLIST_LABEL" >/dev/null 2>&1 || true

echo ""
echo "  [OK] El agente, sus permisos TCC y su auto-inicio han sido desinstalados de este Mac."
echo ""

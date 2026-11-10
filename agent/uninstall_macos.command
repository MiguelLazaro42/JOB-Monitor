#!/usr/bin/env bash
# ==============================================================================
#  JOB-Monitor — Desinstalador de Agente Corporativo para macOS
# ==============================================================================
set -e

INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "===================================================================="
echo "  Desinstalador de Agente de Monitoreo Corporativo (macOS)"
echo "===================================================================="
echo ""

echo "[1/3] Deteniendo LaunchAgent y Watchdog..."
if [ -f "$PLIST_PATH" ]; then
    launchctl unload -w "$PLIST_PATH" >/dev/null 2>&1 || true
    rm -f "$PLIST_PATH"
fi

echo "[2/3] Cerrando procesos activos del agente..."
pkill -f "MonitoreoEmpresa/agent.py" >/dev/null 2>&1 || true

echo "[3/3] Eliminando archivos en $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo ""
echo "  [OK] El agente y su Watchdog han sido desinstalados de este Mac."
echo ""

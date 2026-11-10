#!/usr/bin/env bash
# ==============================================================================
# JOB-Monitor — Instalador Nativo en VPS Linux (systemd + Python .venv)
# No requiere abrir puertos adicionales en el firewall (escucha en 127.0.0.1:5080)
# ==============================================================================
set -e

APP_DIR="/opt/JOB-Monitor"
REPO_SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "================================================================"
echo "  Instalando JOB-Monitor en $APP_DIR (127.0.0.1:5080)"
echo "================================================================"

mkdir -p "$APP_DIR"
cp -R "$REPO_SRC/server" "$APP_DIR/"
cp -R "$REPO_SRC/agent" "$APP_DIR/"
cp -R "$REPO_SRC/deploy" "$APP_DIR/"
mkdir -p "$APP_DIR/data"

if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi

"$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/server/requirements.txt" --quiet

cp "$APP_DIR/deploy/job-monitor.service" /etc/systemd/system/job-monitor.service
systemctl daemon-reload
systemctl enable job-monitor
systemctl restart job-monitor

echo "[OK] Servicio job-monitor activo en http://127.0.0.1:5080"
systemctl status job-monitor --no-pager -l || true


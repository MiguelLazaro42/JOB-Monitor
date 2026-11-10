#!/bin/zsh
# ==============================================================================
# JOB-Monitor — Launcher Rápido de Panel de Administración para macOS (Fase 4)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$PROJECT_DIR/server"
DATA_DIR="$PROJECT_DIR/data"
PORT="${RA_PORT:-5080}"
PANEL_URL="${RA_PANEL_URL:-http://127.0.0.1:$PORT}"

mkdir -p "$DATA_DIR"

# Mini-Función C1.1: Verificar si el servidor ya está respondiendo
is_server_ready() {
  curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1
}

# Mini-Función C1.2: Resolver binario Python del entorno virtual o del sistema
resolve_python() {
  if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    echo "$PROJECT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    echo ""
  fi
}

# Mini-Función C1.3: Iniciar servidor en segundo plano si no está activo y abrir navegador
if ! is_server_ready; then
  PY_BIN="$(resolve_python)"
  if [[ -z "$PY_BIN" ]]; then
    osascript -e 'display alert "Python 3 no encontrado" message "No se encontró el entorno .venv ni python3 en este Mac." as critical' >/dev/null 2>&1 || true
    exit 1
  fi

  cd "$SERVER_DIR"
  nohup "$PY_BIN" app.py --port "$PORT" --no-browser >> "$DATA_DIR/server_macos.log" 2>&1 &

  for _ in {1..15}; do
    if is_server_ready; then
      break
    fi
    sleep 0.3
  done
fi

open "$PANEL_URL"
exit 0

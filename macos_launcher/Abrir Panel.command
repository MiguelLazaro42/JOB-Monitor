#!/bin/zsh
# ==============================================================================
# JOB-Monitor — Launcher Rápido de Panel de Administración para macOS (Fase 4)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$PROJECT_DIR/server"
DATA_DIR="$PROJECT_DIR/data"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

PORT="${RA_PORT:-5080}"
VPS_HOST="${RA_VPS_HOST:-192.168.103.72}"
PANEL_URL="${RA_PANEL_URL:-http://$VPS_HOST:$PORT}"

mkdir -p "$DATA_DIR"

# Mini-Función C1.0: Si el VPS central (192.168.103.72) está en línea, abrir directamente su panel
if curl -fsS --max-time 2 "http://$VPS_HOST:$PORT/api/health" >/dev/null 2>&1; then
  open "$PANEL_URL"
  exit 0
fi

# Mini-Función C1.1: Verificar si el servidor local ya está respondiendo
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
PY_BIN="$(resolve_python)"
if ! is_server_ready; then
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

# Mini-Función C1.4: Asegurar que el Agente Local con Watchdog esté corriendo (candado de instancia única evita duplicados)
if [[ -n "$PY_BIN" && -f "$PROJECT_DIR/agent/agent.py" ]]; then
  nohup "$PY_BIN" "$PROJECT_DIR/agent/agent.py" --server "ws://127.0.0.1:$PORT/ws/agent" >> "$DATA_DIR/agent_macos.log" 2>&1 &
fi

open "$PANEL_URL"
exit 0


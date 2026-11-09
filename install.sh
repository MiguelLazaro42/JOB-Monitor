#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "Creando entorno virtual en $DIR/.venv..."
python3 -m venv .venv
source .venv/bin/activate

echo "Instalando dependencias de Servidor y Agente..."
pip install --upgrade pip --quiet
pip install -r server/requirements.txt -r agent/requirements-agent.txt

echo "Instalación de Fase 1 completada correctamente."


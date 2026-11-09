#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  bash "$DIR/install.sh"
fi

exec "$DIR/.venv/bin/python" "$DIR/server/app.py" "$@"


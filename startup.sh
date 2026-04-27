#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv .venv
  else
    python -m venv .venv
  fi
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x ".venv/Scripts/python" ]; then
  PYTHON=".venv/Scripts/python"
else
  echo "Unable to find Python executable in .venv" >&2
  exit 1
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt

if [ "${GENERATE_SAMPLE_DATA:-0}" = "1" ]; then
  "$PYTHON" scripts/generate_synthetic_data.py
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8501}"
RELOAD="${RELOAD:-0}"

if [ "$RELOAD" = "1" ]; then
  exec "$PYTHON" -m uvicorn promo_guard.app:app --host "$HOST" --port "$PORT" --reload
fi

exec "$PYTHON" -m uvicorn promo_guard.app:app --host "$HOST" --port "$PORT"

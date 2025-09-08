#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-all}"

if [[ "$TARGET" == "backend" || "$TARGET" == "all" ]]; then
  # Ensure venv & deps
  if [[ ! -d ".venv" ]]; then python -m venv .venv; fi
  source .venv/bin/activate
  python -m pip install -U pip
  if [[ -f backend/pyproject.toml ]]; then
    python -m pip install poetry
    (cd backend && poetry install)
  else
    (cd backend && pip install -r requirements.txt)
  fi
fi

if [[ "$TARGET" == "frontend" || "$TARGET" == "all" ]]; then
  (cd frontend && npm install)
fi

# Start DB
docker compose up -d db

if [[ "$TARGET" == "backend" ]]; then
  (cd backend && FLASK_APP=app/main.py flask --app app/main run --host 127.0.0.1 --port 5000 --debug)
elif [[ "$TARGET" == "frontend" ]]; then
  (cd frontend && npm run dev)
else
  # run both with tmux/gnu-screen fallback
  (cd backend && FLASK_APP=app/main.py flask --app app/main run --host 127.0.0.1 --port 5000 --debug) &
  BACK_PID=$!
  (cd frontend && npm run dev) &
  FRONT_PID=$!
  wait $BACK_PID $FRONT_PID
fi

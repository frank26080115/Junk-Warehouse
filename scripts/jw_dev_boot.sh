#!/usr/bin/env bash
set -euo pipefail

# === Config (override via environment or /etc/default/jw_dev_boot) ===
FRONTEND_DIR="${FRONTEND_DIR:/root/Junk-Warehouse/frontend}"
BACKEND_DIR="${BACKEND_DIR:/root/Junk-Warehouse/backend}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"

# If you have a venv for backend, set it here; otherwise leave empty.
BACKEND_VENV="${BACKEND_VENV:/root/Junk-Warehouse/.venv}"

# Command to start backend (edit to match your app entrypoint)
# Examples:
#   gunicorn -w 4 -b 127.0.0.1:5000 app:app
#   uvicorn main:app --host 127.0.0.1 --port 5000
BACKEND_CMD="${BACKEND_CMD:-python -m flask --app app.main:app --debug run --host=0.0.0.0 --port=5000}"

# Use preview to serve the latest build; it’s lightweight and static-like.
FRONTEND_START="${FRONTEND_START:-npx vite preview --host ${FRONTEND_HOST} --port ${FRONTEND_PORT}}"

# === Runtime PIDs ===
frontend_pid=""
backend_pid=""

cleanup() {
  echo "[jw_dev_boot] 🔻 Stopping services..."
  # Kill frontend first (quiet if already gone)
  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
    echo "[jw_dev_boot] Killing frontend (PID ${frontend_pid})"
    kill -INT "${frontend_pid}" 2>/dev/null || true
  fi
  # Kill backend
  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    echo "[jw_dev_boot] Killing backend (PID ${backend_pid})"
    kill -INT "${backend_pid}" 2>/dev/null || true
  fi

  # Give them a moment, then hard-kill if needed
  sleep 2
  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
    kill -KILL "${frontend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    kill -KILL "${backend_pid}" 2>/dev/null || true
  fi
  echo "[jw_dev_boot] ✅ Stopped."
}

trap cleanup INT TERM EXIT

echo "[jw_dev_boot] 🚀 Starting dev stack…"

# === Backend env (optional venv) ===
if [[ -n "${BACKEND_VENV}" && -f "${BACKEND_VENV}/bin/activate" ]]; then
  # shellcheck disable=SC1090
  . "${BACKEND_VENV}/bin/activate"
  echo "[jw_dev_boot] 🧪 Activated backend venv: ${BACKEND_VENV}"
fi

# === Frontend build (always) ===
echo "[jw_dev_boot] 🧱 Building frontend in ${FRONTEND_DIR}…"
pushd "${FRONTEND_DIR}" >/dev/null
if [[ -f package-lock.json ]]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi
npm run build
echo "[jw_dev_boot] ✅ Frontend build complete."

# === Start backend ===
echo "[jw_dev_boot] 🧠 Starting backend: ${BACKEND_CMD}"
pushd "${BACKEND_DIR}" >/dev/null
bash -lc "${BACKEND_CMD}" &
backend_pid=$!
popd >/dev/null

# === Start frontend preview ===
echo "[jw_dev_boot] 🌐 Starting Vite preview: ${FRONTEND_START}"
pushd "${FRONTEND_DIR}" >/dev/null
bash -lc "${FRONTEND_START}" &
frontend_pid=$!
popd >/dev/null

echo "[jw_dev_boot] 🔎 PIDs → backend=${backend_pid}  frontend=${frontend_pid}"
echo "[jw_dev_boot] ✅ Dev stack up. Nginx should proxy to http://${FRONTEND_HOST}:${FRONTEND_PORT}/"

# === If either process dies, exit so systemd can restart ===
wait -n "${backend_pid}" "${frontend_pid}"
status=$?
echo "[jw_dev_boot] ⚠️ One process exited (status ${status}), shutting down the other…"
exit "${status}"

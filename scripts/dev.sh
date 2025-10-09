#!/usr/bin/env bash
# =====================================
# Launch full Junk-Warehouse dev stack
# =====================================
set -e  # Exit immediately on errors

# Move to project root (one up from scripts)
cd "$(dirname "$0")/.."

# ---------- Helper: cleanup ----------
cleanup() {
  echo
  echo "🧹 Cleaning up background processes..."
  # Kill background jobs (npm, etc.)
  jobs -p | xargs -r kill
  echo "✅ All stopped."
}
trap cleanup EXIT

# ---------- Activate Python venv ----------
if [ -d ".venv" ]; then
  echo "🐍 Activating virtual environment..."
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "⚠️  No .venv directory found in project root."
  echo "    Run 'python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt'"
  exit 1
fi

# ---------- Start npm processes ----------
echo "🚀 Starting frontend build..."
npm run -w frontend build &

echo "⚡ Starting frontend dev server..."
npm run -w frontend dev &

# ---------- Start Flask backend ----------
echo "🔥 Starting Flask backend..."
cd backend

# Make Flask discoverable via host 0.0.0.0
python -m flask --app app.main:app --debug run --host=0.0.0.0 --port=5000

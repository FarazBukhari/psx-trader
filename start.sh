#!/bin/bash
# PSX Signal System — launcher
# Usage: ./start.sh [--mock]

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "=== PSX Signal System ==="
echo

# ── Python ─────────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10 python; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "❌  Python not found. Install from https://python.org"
  exit 1
fi
echo "Python : $($PYTHON --version 2>&1)"

# ── pip — always use 'python -m pip' (most portable on macOS) ──────────────
PIP="$PYTHON -m pip"
if ! $PIP --version &>/dev/null; then
  echo "❌  pip not available. Run: $PYTHON -m ensurepip --upgrade"
  exit 1
fi
echo "pip    : $($PIP --version)"
echo

# ── Backend deps ────────────────────────────────────────────────────────────
echo "📦  Installing backend deps…"
cd "$ROOT/backend"
# Try --break-system-packages first (needed on Homebrew/system Python)
# Fall back without it (virtualenv / pyenv users don't need it)
if ! $PIP install -r requirements.txt --break-system-packages -q 2>/dev/null; then
  echo "   (retrying without --break-system-packages)"
  $PIP install -r requirements.txt -q || {
    echo "❌  pip install failed. Try creating a virtualenv:"
    echo "    $PYTHON -m venv .venv && source .venv/bin/activate && ./start.sh $*"
    exit 1
  }
fi
echo "✅  Backend deps OK"
echo

# ── Frontend deps ───────────────────────────────────────────────────────────
if ! command -v npm &>/dev/null; then
  echo "❌  npm not found. Install Node.js from https://nodejs.org"
  exit 1
fi
echo "Node   : $(node --version)  npm: $(npm --version)"

if [ ! -d "$ROOT/frontend/node_modules/.bin/vite" ] && [ ! -f "$ROOT/frontend/node_modules/.bin/vite" ]; then
  echo "📦  Installing frontend deps (first run — takes ~30s)…"
  cd "$ROOT/frontend"
  npm install --legacy-peer-deps 2>&1 | tail -5 || {
    echo "❌  npm install failed."
    exit 1
  }
  echo "✅  Frontend deps OK"
fi
echo

# ── Env ─────────────────────────────────────────────────────────────────────
export PSX_POLL_INTERVAL=5
if [[ "$*" == *--mock* ]]; then
  export PSX_MOCK=true
  echo "⚠️   MOCK mode — using simulated prices"
else
  export PSX_MOCK=false
fi

# ── Backend ─────────────────────────────────────────────────────────────────
echo "🚀  Starting backend  → http://localhost:8000"
cd "$ROOT/backend"
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload 2>&1 &
BACKEND_PID=$!

# Wait for backend to be ready (up to 10s)
echo -n "   Waiting for backend"
for i in $(seq 1 10); do
  sleep 1
  if curl -s http://localhost:8000/health &>/dev/null; then
    echo " ✅"
    break
  fi
  echo -n "."
  if [ "$i" -eq 10 ]; then
    echo " ❌  Backend didn't start. Check errors above."
    kill "$BACKEND_PID" 2>/dev/null
    exit 1
  fi
done

# ── Frontend ─────────────────────────────────────────────────────────────────
echo "🌐  Starting frontend → http://localhost:5173"
cd "$ROOT/frontend"
npm run dev 2>&1 &
FRONTEND_PID=$!

sleep 2
echo
echo "══════════════════════════════════════════"
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173"
echo "  API docs → http://localhost:8000/docs"
echo "══════════════════════════════════════════"
echo
echo "Press Ctrl+C to stop."

cleanup() {
  echo ""
  echo "Stopping…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  echo "Stopped."
}
trap cleanup INT TERM
wait

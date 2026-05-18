#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo " DataLens — Starting up..."
echo "============================================"
echo

# ── 1. Check Python 3.10+ ────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 was not found."
    echo
    echo "Please install Python 3.10 or higher:"
    echo "  macOS:  https://www.python.org/downloads/"
    echo "  Ubuntu: sudo apt install python3 python3-venv"
    echo
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python $PY_VER is too old. Please install Python 3.10 or higher."
    echo "  https://www.python.org/downloads/"
    exit 1
fi

echo "Python $PY_VER found. OK."

# ── 2. Create venv if it does not exist ──────
if [ ! -f ".venv/bin/python" ]; then
    echo "Creating virtual environment in .venv/..."
    python3 -m venv .venv
    echo "Virtual environment created."
else
    echo "Virtual environment already exists. Skipping creation."
fi

# ── 3. Install dependencies only if polars is not importable ──
if ! .venv/bin/python -c "import polars" &>/dev/null; then
    echo "Installing dependencies from requirements.txt..."
    echo "This may take a minute on first run."
    .venv/bin/pip install -r requirements.txt
    echo "Dependencies installed."
else
    echo "Dependencies already installed. Skipping pip."
fi

# ── 4. Start the server ───────────────────────
echo
echo "Starting DataLens server on http://localhost:8000"
echo "Press Ctrl+C to stop."
echo

# Open browser after 2-second delay (background subshell)
(
    sleep 2
    if command -v open &>/dev/null; then
        open http://localhost:8000          # macOS
    elif command -v xdg-open &>/dev/null; then
        xdg-open http://localhost:8000      # Linux
    fi
) &

# Start uvicorn (keeps terminal open with live logs)
.venv/bin/uvicorn web.api:app --host 127.0.0.1 --port 8000

@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  DataLens — Starting up...
echo ============================================
echo.

:: ── 1. Check Python 3.10+ ────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo.
    echo Please install Python 3.10 or higher from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation,
    echo then run this file again.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Capture major.minor version numbers
for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PY_VER=%%V
for /f "tokens=1,2 delims=." %%A in ("!PY_VER!") do (
    set PY_MAJOR=%%A
    set PY_MINOR=%%B
)

if !PY_MAJOR! LSS 3 (
    echo ERROR: Python !PY_VER! is too old. Please install Python 3.10 or higher.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 10 (
    echo ERROR: Python !PY_VER! is too old. Please install Python 3.10 or higher.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python !PY_VER! found. OK.

:: ── 2. Create venv if it does not exist ──────
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv\...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created.
) else (
    echo Virtual environment already exists. Skipping creation.
)

:: ── 3. Install dependencies only if polars is not importable ──
.venv\Scripts\python.exe -c "import polars" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies from requirements.txt...
    echo This may take a minute on first run.
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo Dependencies installed.
) else (
    echo Dependencies already installed. Skipping pip.
)

:: ── 4. Start the server ───────────────────────
echo.
echo Starting DataLens server on http://localhost:8000
echo Press Ctrl+C to stop.
echo.

:: Open browser after 2-second delay (runs in background)
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

:: Start uvicorn (keeps the window open with live logs)
.venv\Scripts\uvicorn.exe web.api:app --host 127.0.0.1 --port 8000

endlocal

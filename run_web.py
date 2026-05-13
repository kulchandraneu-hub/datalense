"""
Launcher for the CSV/Excel comparison web UI.
Starts FastAPI on http://127.0.0.1:8787 and opens the browser.
"""

import sys
import os
from pathlib import Path

# Ensure the project root is on sys.path so all modules resolve correctly.
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Change to project root so relative file paths in the engine work.
os.chdir(ROOT)

import subprocess
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}"


def _check_deps() -> None:
    missing = []
    for pkg in ("fastapi", "uvicorn", "polars", "pydantic"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)


def _open_browser_delayed(url: str, delay: float = 1.5) -> None:
    """Open the browser after a short delay so the server has time to start."""
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    _check_deps()

    # Verify the static frontend exists; warn if not.
    static_index = ROOT / "web" / "static" / "index.html"
    if not static_index.exists():
        print("Note: web/static/index.html not found (Phase 3 frontend not yet built).")
        print("The API is available but there is no UI yet.")
        print()

    print(f"Starting CSV Compare at {URL}")
    print("Press Ctrl+C to stop.\n")

    _open_browser_delayed(URL)

    import uvicorn
    uvicorn.run(
        "web.api:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

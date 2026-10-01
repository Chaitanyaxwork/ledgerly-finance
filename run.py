"""Start the Ledgerly app (backend + frontend) with one command:  python run.py"""
from pathlib import Path
import os
import socket
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PREFERRED_PORT = int(os.environ.get("PORT", "8000"))


def _missing(pkg: str) -> None:
    print(f"\nMissing package: {pkg}\nRun:  python -m pip install -r requirements.txt\n")
    sys.exit(1)


try:
    import uvicorn
except ImportError:
    _missing("uvicorn")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def _pick_port() -> int:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 20):
        if _port_free(port):
            return port
    print(f"No free port found near {PREFERRED_PORT}. Close the other program using it and retry.")
    sys.exit(1)


if __name__ == "__main__":
    # Keep relative paths (.env, SQLite) consistent wherever this is launched from.
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    if not (ROOT / "frontend" / "index.html").exists():
        print("frontend/index.html not found. Keep the 'frontend' folder next to 'backend' and run.py.")
        sys.exit(1)

    # Import here so a missing dependency gives a readable message, not a traceback.
    try:
        from backend.main import app
    except ModuleNotFoundError as exc:
        _missing(exc.name or "a dependency")

    port = _pick_port()
    if port != PREFERRED_PORT:
        print(f"Port {PREFERRED_PORT} is busy — using {port} instead.")
    url = f"http://{HOST}:{port}"
    print(f"Serving frontend from: {ROOT / 'frontend'}")
    print(f"\nLedgerly is running at {url}   (API docs: {url}/docs)\nPress Ctrl+C to stop.\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=port, reload=False)

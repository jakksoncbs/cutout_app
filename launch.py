#!/usr/bin/env python3
"""One-click launcher for the Cutout app.

Double-click this (or the OS shortcut built from it) to:

1. Make sure dependencies are installed (into a private venv on first run).
2. Start the local Cutout web server.
3. Open the drag-drop UI in your default browser.

It needs **Python 3.11+** on the machine; everything else it sets up itself.

Usage:
    python launch.py          # start (or focus an already-running instance)
    python launch.py --stop   # stop a running instance
    python launch.py --no-browser
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Keep the venv and runtime state outside the app folder so a read-only
# install location (e.g. /Applications on macOS) still works.
APP_HOME = Path.home() / ".cutout"
VENV_DIR = APP_HOME / "venv"
STATE_FILE = APP_HOME / "run.json"
MODELS_DIR = HERE / "models"  # optional pre-bundled model(s)

HOST = "127.0.0.1"
PREFERRED_PORT = 8000


# --- dependency bootstrap -------------------------------------------------


def _deps_available() -> bool:
    try:
        import fastapi  # noqa: F401
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import rembg  # noqa: F401
        import uvicorn  # noqa: F401

        return True
    except Exception:
        return False


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ensure_deps() -> None:
    """Install deps if missing, then re-exec inside the venv.

    A guard env var prevents an infinite re-exec loop.
    """
    if _deps_available():
        return
    if os.environ.get("CUTOUT_BOOTSTRAPPED") == "1":
        # We already tried to bootstrap and deps still aren't importable.
        sys.exit(
            "Dependencies could not be loaded. Try deleting "
            f"{VENV_DIR} and launching again."
        )

    print("First run — setting up Cutout (this takes a minute)...")
    APP_HOME.mkdir(parents=True, exist_ok=True)
    py = _venv_python(VENV_DIR)
    if not py.exists():
        print(f"  Creating virtual environment at {VENV_DIR}")
        import venv

        venv.EnvBuilder(with_pip=True).create(VENV_DIR)

    req = HERE / "requirements.txt"
    print("  Installing dependencies...")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"]
    )
    subprocess.check_call([str(py), "-m", "pip", "install", "-q", "-r", str(req)])

    # Re-exec this launcher using the venv's interpreter.
    env = dict(os.environ, CUTOUT_BOOTSTRAPPED="1")
    print("  Starting...")
    os.execve(str(py), [str(py), str(HERE / "launch.py"), *sys.argv[1:]], env)


# --- run-state (so clicking the icon focuses an existing instance) --------


def _read_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
            )
            return str(pid) in out.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _write_state(pid: int, port: int) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"pid": pid, "port": port}))


def _clear_state() -> None:
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def stop() -> int:
    state = _read_state()
    if not state or not _pid_alive(state.get("pid", -1)):
        print("Cutout is not running.")
        _clear_state()
        return 0
    pid = state["pid"]
    print(f"Stopping Cutout (pid {pid})...")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
        else:
            os.kill(pid, 15)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not stop process: {exc}")
        return 1
    _clear_state()
    return 0


# --- networking helpers ---------------------------------------------------


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((HOST, port))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


def _wait_until_up(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((HOST, port)) == 0:
                return True
        time.sleep(0.2)
    return False


# --- run ------------------------------------------------------------------


def run(open_browser: bool = True) -> int:
    # If an instance is already running, just open its UI and exit.
    state = _read_state()
    if state and _pid_alive(state.get("pid", -1)):
        url = f"http://{HOST}:{state['port']}/"
        print(f"Cutout is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    # Point rembg at a bundled model dir if one shipped with the app.
    if MODELS_DIR.is_dir() and "U2NET_HOME" not in os.environ:
        os.environ["U2NET_HOME"] = str(MODELS_DIR)

    os.chdir(HERE)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))

    import uvicorn

    import server  # noqa: WPS433 - imported after deps are ensured

    port = _free_port(PREFERRED_PORT)
    url = f"http://{HOST}:{port}/"
    _write_state(os.getpid(), port)

    def _opener() -> None:
        if _wait_until_up(port) and open_browser:
            webbrowser.open(url)
            print(f"\nCutout is running at {url}")
            print("Leave this window open while you work. Press Ctrl+C to quit.")

    threading.Thread(target=_opener, daemon=True).start()

    try:
        uvicorn.run(server.app, host=HOST, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        _clear_state()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--stop" in args:
        return stop()
    ensure_deps()
    return run(open_browser="--no-browser" not in args)


if __name__ == "__main__":
    raise SystemExit(main())

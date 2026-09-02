"""Convenience launcher for the workshop demo: starts both processes at once,
each with live, unbuffered, labeled console output.

Equivalent to running these in two terminals:

    uv run uvicorn app.server.main:app --reload --reload-dir app
    uv run streamlit run frontend/streamlit_app.py --server.port 80

(``--reload-dir app`` scopes the auto-reload watcher to our own code —
without it, uvicorn also watches data/ and .crew_memory/, and we never want
a CSV drop or a memory write to restart the server; DataCatalog is dynamic
precisely so that isn't necessary.)

Use ``uv run main.py``. Ctrl+C stops both.

NOTE: the UI is bound to port 80 (the standard HTTP port, so the app is
reachable at plain http://localhost with no ``:port`` needed) — that's a
privileged port on macOS/Linux, so this needs to be run with ``sudo``:

    sudo uv run main.py

(or, on macOS, ``sudo $(command -v uv) run main.py`` if ``uv`` isn't on
root's PATH). Change UI_PORT below if you'd rather avoid sudo.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

UI_PORT = 80

# PYTHONUNBUFFERED so child processes flush output immediately instead of
# batching it — otherwise their logs (including CrewAI's own live trace)
# can sit in a buffer for seconds before appearing in this terminal.
_CHILD_ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}


def _stream_output(process: subprocess.Popen, label: str) -> None:
    """Forward one subprocess's combined stdout/stderr to our terminal live,
    prefixed so backend and frontend output stay distinguishable."""
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{label}] {line}", end="", flush=True)


def _spawn(label: str, command: list[str]) -> subprocess.Popen:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered, so _stream_output sees lines as they're written
        env=_CHILD_ENV,
    )
    threading.Thread(target=_stream_output, args=(process, label), daemon=True).start()
    return process


def main() -> None:
    backend = _spawn(
        "backend",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.server.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--reload",
            "--reload-dir",
            "app",
        ],
    )
    time.sleep(2)  # give the backend a moment to boot before the UI's health check runs
    frontend = _spawn(
        "frontend",
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "frontend/streamlit_app.py",
            "--server.port",
            str(UI_PORT),
        ],
    )

    try:
        frontend.wait()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()


if __name__ == "__main__":
    main()

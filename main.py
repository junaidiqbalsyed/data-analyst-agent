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
import signal
import subprocess
import sys
import threading
import time

UI_PORT = 80
BACKEND_PORT = 8000

# PYTHONUNBUFFERED so child processes flush output immediately instead of
# batching it — otherwise their logs (including CrewAI's own live trace)
# can sit in a buffer for seconds before appearing in this terminal.
_CHILD_ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}


def _free_port(port: int) -> None:
    """Kill anything already listening on ``port`` before we try to bind it.

    uvicorn's ``--reload`` spawns a separate worker process (via
    ``multiprocessing``) underneath its reloader. If a previous run's
    reloader ever dies uncleanly — a force-closed terminal, a crashed
    shell, a `kill -9` on the wrong pid — that worker can survive on its
    own, reparented to init, silently holding the port for every run after
    that (this has bitten us twice: `[Errno 48] Address already in use` on
    a totally fresh launch). Rather than rely on clean shutdown alone,
    sweep the port clear before we start.
    """
    try:
        result = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return
    pids = [pid for pid in result.stdout.split() if pid]
    if not pids:
        return
    print(f"Port {port} is already in use by leftover process(es) {', '.join(pids)} — clearing it first.")
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)


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
        # Its own process group, not ours — so a signal we send later can
        # target the whole group (reloader + its worker subprocess), not
        # just the one direct child. See _terminate_group below.
        start_new_session=True,
    )
    threading.Thread(target=_stream_output, args=(process, label), daemon=True).start()
    return process


def _terminate_group(process: subprocess.Popen) -> None:
    """Signal a spawned process's *entire* process group, not just itself —
    uvicorn --reload's worker subprocess lives in the same group, and a
    plain process.terminate() only reaches the reloader, which is exactly
    how we got orphaned workers before (see _free_port)."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass  # already gone
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main() -> None:
    _free_port(BACKEND_PORT)
    _free_port(UI_PORT)

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
            str(BACKEND_PORT),
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
        _terminate_group(backend)
        _terminate_group(frontend)


if __name__ == "__main__":
    main()

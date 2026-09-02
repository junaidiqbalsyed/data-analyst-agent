# Windows Setup

One file, double-click it: [`setup_windows.bat`](setup_windows.bat).

It's a real Windows executable (Explorer runs `.bat` files the same way it
runs a `.exe` — no compiling needed). It will:

1. Install [`uv`](https://docs.astral.sh/uv/) if it isn't already on your
   machine, and refresh this terminal's `PATH` so you don't have to reopen it.
2. Run `uv sync` to create `.venv` and install every dependency.
3. If `uv` isn't available or `uv sync` fails for any reason, fall back
   automatically to `python -m venv .venv` + `pip install` of the same
   dependencies — no manual steps needed either way.

## Run it

- **Double-click** `setup_windows.bat` in File Explorer, **or**
- From a terminal, in this folder: `setup_windows.bat`

When it finishes, it prints the next steps. In short:

```bat
copy .env.example .env
REM ...then edit .env with your LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

.venv\Scripts\activate
python main.py
```

## Troubleshooting

- **"uv still isn't recognized"** — close the terminal, open a new one, and
  re-run `setup_windows.bat`. (The script tries to refresh `PATH` itself,
  but a fresh terminal always picks up a brand-new install cleanly.)
- **"Could not create a virtual environment"** — Python isn't installed.
  Get it from [python.org/downloads](https://www.python.org/downloads/)
  (tick **"Add python.exe to PATH"** during install), then re-run the script.
- Anything else: run `setup_windows.bat` from a terminal (not by
  double-click) so you can read the error text, and re-run once it's fixed.

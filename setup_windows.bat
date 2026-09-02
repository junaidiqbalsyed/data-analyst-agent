@echo off
REM =====================================================================
REM  Windows setup script for the Multi-Level CrewAI Analytics Chatbot.
REM
REM  What it does, in order:
REM    1. Installs uv if it isn't already on PATH.
REM    2. Refreshes PATH in THIS terminal session (no reopen needed).
REM    3. Runs "uv sync" to create .venv and install dependencies.
REM    4. If uv isn't available or "uv sync" fails: falls back to a
REM       plain "python -m venv .venv" + pip install of the same deps.
REM
REM  Double-click this file, or run it from a terminal: setup_windows.bat
REM =====================================================================
setlocal EnableDelayedExpansion

echo ============================================================
echo   Multi-Level CrewAI Analytics Chatbot - Windows Setup
echo ============================================================
echo.

REM --- Step 1: make sure uv is installed ---------------------------------
where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/3] uv is already installed.
    goto :try_uv_sync
)

echo [1/3] uv not found - installing it now...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

REM --- Step 2: refresh PATH for this session, without reopening the terminal
echo       Refreshing PATH for this terminal...
set "USER_PATH="
set "SYS_PATH="
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
set "PATH=%SYS_PATH%;%USER_PATH%;%USERPROFILE%\.local\bin"

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo       uv still isn't recognized in this window.
    echo       Close this terminal, open a NEW one, and re-run setup_windows.bat.
    echo       Falling back to plain venv + pip for now...
    goto :venv_fallback
)

:try_uv_sync
echo [2/3] Running "uv sync"...
uv sync
if %ERRORLEVEL% EQU 0 (
    echo       Done - dependencies installed via uv.
    goto :done
)
echo       "uv sync" failed - falling back to plain venv + pip.

:venv_fallback
echo [3/3] Setting up a virtual environment with pip...
if not exist ".venv" (
    echo       Creating .venv ...
    python -m venv .venv 2>nul || py -m venv .venv 2>nul
    if not exist ".venv\Scripts\activate.bat" (
        echo.
        echo ERROR: Could not create a virtual environment.
        echo        Install Python 3.13+ from https://www.python.org/downloads/
        echo        ^(tick "Add python.exe to PATH" during install^), then re-run this script.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install "crewai>=1.15.18" "duckdb>=1.5.5" "fastapi>=0.141.1" "openai>=2.54.0" "pydantic-settings>=2.15.0" "requests>=2.34.2" "sse-starlette>=3.4.8" "streamlit>=1.63.0" "uvicorn[standard]>=0.52.4"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: pip install failed - see the errors above.
    pause
    exit /b 1
)

:done
echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo Next steps:
echo   1. Copy .env.example to .env and fill in LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
echo   2. In this terminal, activate the environment:
echo        .venv\Scripts\activate
echo   3. Run the app:
echo        python main.py
echo.
pause

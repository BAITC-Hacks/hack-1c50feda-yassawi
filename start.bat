@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto ready
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
  if not errorlevel 1 goto install
)
where python >nul 2>nul
if not errorlevel 1 (
  python -m venv .venv
  if not errorlevel 1 goto install
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
  if not errorlevel 1 goto install
)
echo Python 3.11 or newer is required. Install it from python.org and run again.
pause
exit /b 1
:install
echo Installing the pinned dependencies. Internet is needed only for this setup.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; import pandas, numpy"
if errorlevel 1 goto install
echo.
echo Open http://127.0.0.1:8765 in your browser.
echo Leave this window open. Press Ctrl+C to stop the server.
".venv\Scripts\python.exe" server.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup or launch failed. Read the error above and README.md.
pause
exit /b 1

@echo off
setlocal EnableExtensions
title CoIoED Head Office - Local Launch (Python Unlock)
color 0A
set "APP_DIR=%~dp0"
if not exist "%APP_DIR%app.py" (
  for /d %%D in ("%APP_DIR%*") do if exist "%%~fD\app.py" set "APP_DIR=%%~fD\"
)
echo.
echo ============================================================
echo        CoIoED Head Office - Secure Local Launcher
echo ============================================================
echo.
if not exist "%APP_DIR%app.py" (
  echo ERROR: app.py could not be found.
  echo Extract the entire ZIP, then run this BAT from the same folder as app.py.
  goto failure
)
cd /d "%APP_DIR%"
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3"
)
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
  )
)
if not defined PY_CMD (
  echo ERROR: Python 3.11 or newer was not found on this computer.
  echo Ask IT to install approved Python, then run this launcher again.
  goto failure
)
echo Python detected:
%PY_CMD% --version
if not exist "%APP_DIR%data\coioed.db" (
  echo.
  echo Unlocking preloaded encrypted case register with Windows CNG.
  echo No PowerShell script execution or policy changes are needed.
  if not exist "%APP_DIR%UNLOCK_PRIVATE_DATA.py" (
    echo ERROR: UNLOCK_PRIVATE_DATA.py missing from application folder.
    goto failure
  )
  %PY_CMD% -u "%APP_DIR%UNLOCK_PRIVATE_DATA.py"
  if errorlevel 1 goto failure
)
if not exist "%APP_DIR%data\coioed.db" (
  echo ERROR: Case database not installed. Refusing to start a blank database.
  goto failure
)
echo.
echo Preparing local database...
%PY_CMD% -c "import app;app.init_db()"
if errorlevel 1 goto failure
%PY_CMD% -c "import sqlite3,app,sys;c=sqlite3.connect(str(app.DB));sys.exit(0 if c.execute('SELECT 1 FROM users LIMIT 1').fetchone() else 1)" >nul 2>nul
if errorlevel 1 (
  echo First launch: create your administrator account.
  %PY_CMD% -u app.py --init-admin
  if errorlevel 1 goto failure
)
echo.
echo Open a web browser on THIS computer and type:
echo    http://127.0.0.1:8787/
echo.
echo Keep this window open while using CoIoED. Ctrl+C stops the server.
echo Do not expose real case records on an unapproved LAN or public network.
echo.
%PY_CMD% -u app.py --host 127.0.0.1 --port 8787
if errorlevel 1 goto failure
exit /b 0
:failure
echo.
echo Launch did not finish. Photograph this error screen, not any personal data.
pause
exit /b 1
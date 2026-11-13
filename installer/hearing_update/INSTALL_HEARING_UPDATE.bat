@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo  CoIoED Hearing Schedule Update - Existing Installation
 echo ================================================================
echo Close the CoIoED server first. Your live database is not modified.
echo.
where py >nul 2>nul
if not errorlevel 1 (
 py -3 installer.py
) else (
 python installer.py
)
if errorlevel 1 (
 echo UPDATE FAILED. Read the error above and check rollback status.
) else (
 echo Update completed. Start your original LAUNCH_COIOED.bat.
)
pause
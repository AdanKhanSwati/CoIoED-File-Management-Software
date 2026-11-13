@echo off
setlocal
cd /d "%~dp0"
echo CoIoED hearing update rollback - close CoIoED first.
where py >nul 2>nul
if not errorlevel 1 (
 py -3 installer.py --rollback
) else (
 python installer.py --rollback
)
pause
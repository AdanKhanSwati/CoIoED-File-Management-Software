@echo off
setlocal EnableExtensions
title CoIoED Hearing Schedule Update v13.4
set "ROOT=%~dp0"
echo ================================================================
echo   CoIoED Hearing Schedule Update - Existing Installation
echo ================================================================
echo.
if not exist "%ROOT%installer.py" (
  echo ERROR: installer.py is missing from this folder.
  echo.
  echo This batch file is ONLY a launcher. It cannot install by itself.
  echo Extract the COMPLETE GitHub repository ZIP first.
  echo Then open installer\hearing_update and run the batch file THERE.
  echo.
  echo Required files next to this batch file:
  echo   installer.py
  echo   payload\hearing_v2.py
  echo   payload\notice_export.py
  echo   payload\test_notice_move_integration.py
  echo   payload\test_clubbed_parentage_export.py
  echo.
  echo DO NOT copy the batch file alone into the existing CoIoED folder.
  echo GitHub repository: https://github.com/AdanKhanSwati/CoIoED-File-Management-Software
  echo.
  pause
  exit /b 2
)
for %%F in (hearing_v2.py notice_export.py test_notice_move_integration.py test_clubbed_parentage_export.py) do (
  if not exist "%ROOT%payload\%%F" (
    echo ERROR: Missing payload\%%F
    echo Extract the COMPLETE update folder, including its payload directory.
    pause
    exit /b 2
  )
)
echo Update files located. Close the CoIoED server before proceeding.
echo Your live database is NOT modified by this update.
echo.
pushd "%ROOT%" || (echo ERROR: Cannot open installer directory.& pause & exit /b 2)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%ROOT%installer.py"
) else (
  python "%ROOT%installer.py"
)
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" (
 echo.
 echo UPDATE FAILED. Review the error above.
) else (
 echo.
 echo Update completed. Start your original LAUNCH_COIOED.bat.
)
pause
exit /b %RESULT%

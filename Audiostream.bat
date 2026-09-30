@echo off
cd /d "%~dp0"
echo Installing Audiostream Room...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Opening Audiostream Room.
where pythonw >nul 2>&1
if errorlevel 1 goto console
start "" pythonw -m audiostream room
exit /b 0

:console
python -m audiostream room
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Audiostream Room did not start. Leave this window open and read the message above.
pause
exit /b 1

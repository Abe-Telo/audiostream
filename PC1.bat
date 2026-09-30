@echo off
cd /d "%~dp0"
echo Installing the Windows audio library...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Opening Audiostream...
where pythonw >nul 2>&1
if errorlevel 1 goto console
start "" pythonw -m audiostream pc1
exit /b 0

:console
python -m audiostream pc1
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Audiostream did not start. Leave this window open and read the message above.
pause
exit /b 1

@echo off
cd /d "%~dp0"
if exist "%~dp0AudiostreamPC2.exe" (
  start "" "%~dp0AudiostreamPC2.exe"
  exit /b 0
)
echo Installing the Windows audio library...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Opening Audiostream PC2...
where pythonw >nul 2>&1
if errorlevel 1 goto console
start "" pythonw -m audiostream pc2
exit /b 0

:console
python -m audiostream pc2
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Audiostream did not start. Leave this window open and read the message above.
pause
exit /b 1

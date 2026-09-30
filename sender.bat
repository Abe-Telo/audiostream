@echo off
cd /d "%~dp0"
echo Installing the Windows audio library...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Sending this PC's system audio to 192.168.1.198
python -m audiostream sender --host 192.168.1.198
pause
exit /b 0

:fail
echo.
echo Install failed. Leave this window open and read the message above.
pause
exit /b 1

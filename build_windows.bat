@echo off
cd /d "%~dp0"
echo This builds AudiostreamPC1.exe and AudiostreamPC2.exe into the dist folder.
python -m pip install pyinstaller "pyaudiowpatch>=0.2.12.8"
if errorlevel 1 goto fail
python -m PyInstaller --noconfirm --clean --windowed --onefile --name AudiostreamPC1 --icon audiostream\icon.ico --add-data "audiostream\icon.ico;audiostream" --hidden-import pyaudiowpatch --hidden-import audiostream.win_audio --distpath dist --workpath build\pc1 packaging\pc1_entry.py
if errorlevel 1 goto fail
python -m PyInstaller --noconfirm --clean --windowed --onefile --name AudiostreamPC2 --icon audiostream\icon.ico --add-data "audiostream\icon.ico;audiostream" --hidden-import pyaudiowpatch --hidden-import audiostream.win_audio --distpath dist --workpath build\pc2 packaging\pc2_entry.py
if errorlevel 1 goto fail
echo.
echo Built:
echo   dist\AudiostreamPC1.exe
echo   dist\AudiostreamPC2.exe
exit /b 0

:fail
echo Build failed.
pause
exit /b 1

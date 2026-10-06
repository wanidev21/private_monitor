@echo off
rem PC Monitor exe build (PyInstaller onedir, no console window)
rem Usage: build.bat          (pause at the end)
rem        build.bat /nopause (for automation)
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe

if not exist %PY% (
    echo [0/4] Creating venv with python.org Python 3.14...
    C:\Python314\python.exe -m venv .venv || goto :error
)

echo [1/4] Installing dependencies...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

echo [2/4] Creating icon...
if not exist assets\icon.ico (
    %PY% assets\make_icon.py || goto :error
)

echo [3/4] Building exe...
%PY% -m PyInstaller --noconfirm --clean --windowed --name PCMonitor --icon=assets\icon.ico ^
  --hidden-import=pynvml ^
  --hidden-import=pystray._win32 ^
  --hidden-import=PIL._tkinter_finder ^
  --hidden-import=uvicorn.logging ^
  --hidden-import=uvicorn.loops.auto ^
  --hidden-import=uvicorn.protocols.http.auto ^
  --hidden-import=uvicorn.protocols.websockets.auto ^
  --hidden-import=uvicorn.lifespan.on ^
  --add-data "templates;templates" ^
  --add-data "assets\icon.ico;assets" ^
  main.py || goto :error

echo [4/4] Done!
echo Output: dist\PCMonitor\PCMonitor.exe
if /i not "%~1"=="/nopause" pause
exit /b 0

:error
echo Build failed.
if /i not "%~1"=="/nopause" pause
exit /b 1

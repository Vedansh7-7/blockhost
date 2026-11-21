@echo off
rem Builds dist\BlockHost.exe (Windows). Run from the repo root.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements-build.txt
)
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name BlockHost ^
  --icon app\icon.ico --add-data "app\ui;ui" --paths app app\main.py
echo.
echo Built: %~dp0dist\BlockHost.exe

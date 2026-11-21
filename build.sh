#!/usr/bin/env bash
# Builds dist/BlockHost.app (macOS). Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements-build.txt
fi
.venv/bin/pyinstaller --noconfirm --clean --windowed --name BlockHost \
  --icon app/icon.ico --osx-bundle-identifier io.github.blockhost.launcher \
  --add-data "app/ui:ui" --paths app app/main.py
echo "Built: $(pwd)/dist/BlockHost.app"

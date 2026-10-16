#!/bin/bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install Python 3.10 or newer from python.org."
    exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-dev.txt

QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v

python3 -m PyInstaller --noconfirm --clean FART.spec

echo
echo "Built successfully: dist/FART.app"

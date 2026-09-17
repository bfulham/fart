#!/bin/bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install Python 3.10 or newer from python.org."
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "This python3 does not have tkinter (Tcl/Tk) support and would produce a"
    echo "FART.app that crashes on launch. Install Python from"
    echo "https://www.python.org/downloads/macos/ instead (its installer includes Tk)."
    exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-dev.txt

python3 -m unittest discover -s tests -v

python3 -m PyInstaller --noconfirm --clean FART.spec

echo
echo "Built successfully: dist/FART.app"

#!/bin/bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install Python 3.10 or newer from python.org."
    read -n 1 -s -r -p "Press any key to exit..."
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "This python3 does not have tkinter (Tcl/Tk) support, so the GUI cannot start."
    echo "Some Python distributions (for example a plain pyenv build) omit Tk by default."
    echo "Install Python from https://www.python.org/downloads/macos/ instead (its installer"
    echo "includes Tk), or rebuild your existing distribution with Tcl/Tk support."
    read -n 1 -s -r -p "Press any key to exit..."
    exit 1
fi

python3 -m pip install -r requirements.txt
python3 fart.py

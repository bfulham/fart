#!/bin/bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install Python 3.10 or newer from python.org."
    read -n 1 -s -r -p "Press any key to exit..."
    exit 1
fi

python3 -m pip install -r requirements.txt
python3 -m fart

#!/bin/sh
# Start the billing app on Linux/macOS.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python app.py

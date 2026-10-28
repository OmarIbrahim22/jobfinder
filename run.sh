#!/usr/bin/env bash
# Runs the job finder, creating its virtualenv on first use. Arguments are passed through.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip -q install -r requirements.txt
fi
[ -f config.toml ] || cp config.example.toml config.toml  # first run: start from the example settings
exec .venv/bin/python jobfinder.py "$@"

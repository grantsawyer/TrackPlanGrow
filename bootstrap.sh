#!/usr/bin/env bash
# One-time setup: creates the Python environment OUTSIDE this repo (in the
# data folder) and installs fintrack into it, then runs `fintrack init`.
# Safe to re-run; it reinstalls the package into the existing environment.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
FINTRACK_HOME="${FINTRACK_HOME:-$HOME/.fintrack}"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.12}"

if [ ! -x "$PYTHON" ]; then
  echo "Python 3.12 not found at $PYTHON. Install it with: brew install python@3.12" >&2
  exit 1
fi

mkdir -p -m 700 "$FINTRACK_HOME"
if [ ! -d "$FINTRACK_HOME/venv" ]; then
  "$PYTHON" -m venv "$FINTRACK_HOME/venv"
fi

# Editable install: the venv runs the code straight from this repo, so code
# changes take effect without reinstalling.
"$FINTRACK_HOME/venv/bin/pip" install --quiet --upgrade pip
"$FINTRACK_HOME/venv/bin/pip" install --quiet -e "$REPO_DIR[dev]"

FINTRACK_HOME="$FINTRACK_HOME" "$FINTRACK_HOME/venv/bin/fintrack" init

echo
echo "Installed. To run 'fintrack' from anywhere, add this line to ~/.zshrc:"
echo "  export PATH=\"$FINTRACK_HOME/venv/bin:\$PATH\""

#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.9+ from https://www.python.org/downloads/ first. / 请先安装 Python。"
  read -r _unused
  exit 1
fi
python3 start.py

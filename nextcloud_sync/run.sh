#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

exec python3 -u /app/daemon.py

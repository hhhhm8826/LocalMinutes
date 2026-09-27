#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
export MINUTES_WEB_DIR="${MINUTES_WEB_DIR:-$PWD/apps/web/dist}"
data_dir=$(.venv/bin/python -c 'from meeting_minutes.settings import Settings; print(Settings().data_dir)')
exec .venv/bin/python -m meeting_minutes.server --data-dir "$data_dir"

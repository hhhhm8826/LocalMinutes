#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mode=${1:-fast}
shift || true
web_check() {
  source "${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes/toolchain/env.sh"
  (cd apps/web && npm run check && ./node_modules/.bin/playwright test "$@")
}
case "$mode" in
  fast)
    .venv/bin/ruff check src tests
    .venv/bin/pytest -m 'not integration and not model and not live_codex and not slow' "$@"
    source "${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes/toolchain/env.sh"
    (cd apps/web && npm run check)
    ;;
  affected)
    [[ $# -gt 0 ]] || { echo 'affected requires explicit test paths' >&2; exit 2; }
    .venv/bin/pytest "$@" -m 'not model and not live_codex and not slow'
    ;;
  integration) .venv/bin/pytest -m 'integration and not model and not live_codex and not slow' "$@" ;;
  e2e) web_check "$@" ;;
  model|live-codex|soak) .venv/bin/python scripts/check-costly.py "$mode" "$@" ;;
  release)
    [[ $# == 0 ]] || { echo 'release does not accept scope overrides' >&2; exit 2; }
    .venv/bin/ruff check src tests
    .venv/bin/pytest -m 'not model and not live_codex and not slow'
    web_check
    .venv/bin/python scripts/check-evidence.py
    ;;
  *) printf '%s\n' "검사 모드 미구현: $mode" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# 비밀번호/토큰은 소유자의 로컬 터미널에서만 입력한다. 로그에 저장하지 않는다.
set -euo pipefail
source /home/cs2023/.local/share/local-meeting-minutes/toolchain/env.sh
case "${1:-}" in
  development)
    install -d -m 700 /home/cs2023/.codex
    exec codex login
    ;;
  runtime)
    install -d -m 700 /home/cs2023/.local/share/local-meeting-minutes/codex-home
    exec env CODEX_HOME=/home/cs2023/.local/share/local-meeting-minutes/codex-home codex login
    ;;
  huggingface)
    exec /home/cs2023/src/local-meeting-minutes/.venv/bin/hf auth login
    ;;
  *) printf '%s\n' 'usage: bash scripts/owner-login.sh development|runtime|huggingface' >&2; exit 2 ;;
esac

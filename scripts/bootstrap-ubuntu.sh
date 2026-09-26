#!/usr/bin/env bash
# 준비 전용. 관리자 패키지는 --system-plan으로 안내하고 자동 실행하지 않는다.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ${1:-} == --system-plan ]]; then
  printf '%s\n' 'sudo apt-get update' \
    'sudo apt-get install --no-install-recommends ffmpeg git ca-certificates curl build-essential python3.12-venv python3.12-dev pkg-config'
  exit 0
fi
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || { echo 'Ubuntu 24.04 x86_64 필요' >&2; exit 2; }
[[ $PWD != /mnt/* && $EUID != 0 ]] || { echo 'Linux 파일시스템, 비관리자 실행 필요' >&2; exit 2; }
[[ -f uv.lock && -f pyproject.toml && -f .node-version ]] || { echo 'BLOCKED: 승인된 의존성 버전/잠금 파일 미확정' >&2; exit 3; }
toolchain_env="$HOME/.local/share/local-meeting-minutes/toolchain/env.sh"
[[ ! -f $toolchain_env ]] || source "$toolchain_env"
command -v uv >/dev/null
command -v node >/dev/null
[[ $(node --version) == "v$(cat .node-version)" ]] || { echo 'Node 고정 버전 불일치' >&2; exit 3; }
uv sync --frozen --group preflight
[[ ! -f package-lock.json ]] || npm ci --ignore-scripts --no-audit --no-fund

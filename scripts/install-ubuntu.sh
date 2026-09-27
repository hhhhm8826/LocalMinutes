#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
if [[ ${1:-} == --system-plan ]]; then
  printf '%s\n' 'sudo apt-get update' 'sudo apt-get install --no-install-recommends ffmpeg git ca-certificates curl build-essential python3.12-venv python3.12-dev pkg-config bubblewrap'
  exit 0
fi
[[ $# == 0 ]] || { echo 'usage: install-ubuntu.sh [--system-plan]' >&2; exit 2; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || { echo 'Ubuntu 24.04 x86_64가 필요합니다.' >&2; exit 2; }
[[ $EUID != 0 && $PWD != /mnt/* ]] || { echo 'Linux 파일시스템에서 일반 사용자로 설치하세요.' >&2; exit 2; }
python3.12 -c 'import venv, ssl'
data_dir="${MINUTES_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes}"
[[ $data_dir == /* ]] || { echo '데이터 경로는 절대 경로여야 합니다.' >&2; exit 2; }
mkdir -p -- "$data_dir"
exec 9>"$data_dir/app.lock"
exec 8>"$data_dir/worker.lock"
flock -n 9 && flock -n 8 || { echo '앱과 작업 관리자를 정지한 뒤 설치하세요.' >&2; exit 2; }
for command in ffmpeg ffprobe git; do
  command -v "$command" >/dev/null || { echo "필수 시스템 도구 없음: $command (--system-plan 참고)" >&2; exit 2; }
done
python3.12 scripts/setup-toolchain.py
python3.12 scripts/setup-youtube.py
toolchain="${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes/toolchain"
source "$toolchain/env.sh"
uv sync --frozen --no-dev --python python3.12
if [[ ! -f apps/web/dist/index.html ]]; then
  (cd apps/web && npm ci --no-audit --no-fund && npm run build)
fi
.venv/bin/python -c 'from meeting_minutes.settings import Settings; from meeting_minutes.storage import make_engine,migrate; s=Settings(); s.prepare(); e=make_engine(s.database_path); migrate(e); e.dispose()'
printf '%s\n' '설치 완료. 모델 준비·런타임 로그인 후 scripts/doctor.sh로 확인하세요.' '실행: bash scripts/run.sh'

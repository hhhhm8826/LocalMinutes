# 로컬 회의록

Ubuntu 24.04 x86_64용 개인 로컬 웹 앱입니다. Windows에서는 WSL Ubuntu에서 실행하고 브라우저로 사용합니다. CPU 전사·화자 구분 후, 사용자가 동의한 회의의 텍스트·메타데이터만 별도 구독 Codex CLI로 보내 한글 회의록을 만듭니다. 음성·영상·화자 임베딩은 로컬에 남습니다.

## 구현된 기능

- 음성·영상 업로드, 최대 2GB(2,000,000,000바이트)·120분, 오디오 트랙 선택, 발화자 자동/1~12명.
- `large-v3-turbo` CPU INT8 전사, 한·영 정렬, pyannote 화자 구분과 병합 후보.
- 영속 FIFO, 처리 상태, 취소·재시도·중단 복구.
- 근거가 연결된 한글 회의록, 전사·화자 수정/되돌리기, 초안·확정·버전 관리.
- 검색, 근거 음성 재생, Markdown/텍스트 내보내기, 삭제·보관 설정.
- 루프백 소유자 인증, 설치·진단·백업/복원, 선택적 systemd 사용자 서비스.

MVP 검증은 완료됐으며 이후 작업 상태는 [마일스톤](docs/milestone.md)에 기록합니다. Git에는 소스·테스트·고정 의존성·문서·이용 고지가 포함됩니다. 개인 실행 증거 `.workflow`, 회의 데이터, 모델 가중치, 로그인 정보, 생성 번들은 포함되지 않습니다.

## 설치·실행

Ubuntu/WSL의 Linux 파일시스템에 저장소를 checkout하거나 번들을 풀고, 해당 디렉터리에서 일반 사용자로 실행합니다. `/mnt/c`·`/mnt/d` 설치는 지원하지 않습니다. 준비 기준은 RAM 32GB·CPU 6코어 이상이며 처리 속도는 장비에 따라 달라집니다.

```bash
bash scripts/install-ubuntu.sh --system-plan  # 필요한 관리자 패키지 명령 확인
# 필요한 시스템 패키지를 설치한 뒤:
bash scripts/install-ubuntu.sh               # 고정 도구·환경 설치, 필요 시 웹 빌드
.venv/bin/hf auth login
bash scripts/prepare-models.sh --accept-model-terms
bash scripts/login-codex.sh                  # 별도 런타임 구독 로그인
bash scripts/doctor.sh
bash scripts/run.sh
```

모델 준비 전 [pyannote 이용조건](https://huggingface.co/pyannote/speaker-diarization-community-1)을 소유자가 확인·동의해야 합니다. 옵션은 웹 동의나 로그인을 대신하지 않습니다. 모델 리비전·해시는 `src/meeting_minutes/model_manifest.json`, 의존성·모델 고지는 [THIRD_PARTY](notices/THIRD_PARTY.md)에 있습니다.

브라우저에서 `http://127.0.0.1:8765`를 열고 `~/.config/local-meeting-minutes/owner-key`의 값을 입력합니다. 키·토큰을 채팅이나 Git에 넣지 마세요. Windows 실행 진입점은 `powershell -File scripts/dev-windows.ps1 -Distro Ubuntu -Repo <Linux-절대경로>`입니다.

## 사용과 운영

파일을 올리고 제목·언어·회의 일시·발화자 수·외부 텍스트 처리 허용을 설정합니다. 허용하지 않으면 전사만 저장합니다. 차단된 앞 작업은 재시도·전사만 종료·취소로 해소합니다. 상세 화면에서 근거 음성을 확인하고 전사·화자를 수정한 뒤 회의록을 검토·확정합니다. 수정과 재생성은 새 버전으로 남습니다.

```bash
bash scripts/stop.sh
bash scripts/backup.sh /absolute/new-backup.tar.gz
bash scripts/restore.sh /absolute/new-backup.tar.gz /absolute/new-data-directory
MINUTES_DATA_DIR=/absolute/new-data-directory bash scripts/run.sh
bash scripts/install-service.sh  # 서비스 파일 생성; 활성화는 출력 안내를 따름
```

백업 전 앱을 정지합니다. 백업은 인증·로그인 세션을 제외하고 회의 내용을 포함합니다. 복원은 파일 크기·해시 manifest 검사 후 존재하지 않는 새 경로에만 수행합니다. 기본 보관은 직접 삭제 전까지 유지하며 자동 삭제는 설정에서 켭니다. 별도 백업은 앱 삭제와 함께 지워지지 않습니다.

기본 데이터·설정·캐시는 HOME/XDG를 따르며 `MINUTES_DATA_DIR`, `MINUTES_CONFIG_DIR`, `MINUTES_CACHE_DIR`로 변경할 수 있습니다. 기본 구독 인증 경로는 `~/.local/share/local-meeting-minutes/codex-home`입니다. 다른 장비/사용자에서는 인증을 복사하지 않고 다시 로그인합니다.

## 개발·검증

설치 후 도구 환경을 읽고 개발 의존성을 준비합니다.

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes/toolchain/env.sh"
uv sync --frozen
(cd apps/web && npm ci)
bash scripts/check.sh affected tests/test_foundation.py
```

[작업 규약](docs/workflow.md)에 전체 검사·검수·증거 관리 절차가 있습니다. **120분 장시간 시험은 전체/릴리즈 검사와 별개이며 사용자가 명시적으로 지시할 때만 실행합니다.** 제품의 120분 입력 지원은 유지합니다.

실제 한국어·영어 처리와 Codex 생성, 웹 흐름, 별도 HOME 설치·복원을 확인했습니다. 자연 12인 정확도·CER/WER/DER는 미평가이며 혼용·겹친 발화·회의록 의미는 검토가 필요합니다. 진단의 캐시/로그인 표시가 실제 추론·원격 접근을 보증하지 않습니다. 앱 사용량은 계정 전체 구독 잔여량이 아닙니다. 다른 LLM/API 키 폴백, 실시간 STT, 다중 사용자, 외부 발행은 제공하지 않습니다.

# 작업·운영 규약

## 문서와 실행 환경

- `README.md`는 기능 소개·최소 설치·기본 사용법·문서 링크만 유지합니다. `docs/milestone.md`는 현재 구현·유지 조건·남은 인수, 이 문서는 설정·운영·개발·검수 절차, `AGENTS.md`는 영문 진입 지침입니다.
- 변경 시 관련 문서의 기존 내용을 직접 최신화합니다. 마일스톤마다 완료 계획·중복 체크리스트를 현재 기능 요약으로 통합하고 상세 변경 사유·커밋별 이력은 Git/`.workflow`에 둡니다. 미완료 항목은 제거하지 않습니다. 문서만 바꾸면 추가 검수·전체 테스트는 필요하지 않습니다.
- 실제 원본·개발·테스트는 Ubuntu/WSL의 `/home/cs2023/src/local-meeting-minutes`, Windows 사본은 `D:\LocalMinutes`입니다. 새 환경은 경로를 다시 등록합니다. 데이터·모델·인증은 Linux 파일시스템에 둡니다.
- 기존 **Astra medium 마스터 + Sol 5.6 high 검증자** 두 세션만 사용합니다. UUID는 `.workflow/session-registry.json`에서 읽으며 추가 세션·재귀 에이전트는 만들지 않습니다. Windows 기존 세션/큐 사용은 승인된 예외입니다.
- 관리자 설치·로그인·이용조건·권한 변경은 기존 승인 범위를 먼저 확인합니다. 새 계정 작업은 소유자가 수행하며 비밀을 채팅에 넣지 않습니다. 원격 push·외부 공개는 별도 지시가 필요합니다.

## 설정과 운영

### 실행 경로와 Windows

기본 데이터·설정·캐시는 HOME/XDG를 따르며 `MINUTES_DATA_DIR`, `MINUTES_CONFIG_DIR`, `MINUTES_CACHE_DIR`로 변경합니다. 실제 서버는 작업 처리기를 활성화해야 하며 `MINUTES_WORKER_ENABLED=false`는 격리된 화면 시험 전용입니다.

```powershell
# 실제 Linux 저장소 경로로 바꿉니다.
powershell -File scripts/dev-windows.ps1 -Distro Ubuntu -Repo "/home/<사용자>/src/local-meeting-minutes"
```

### AI 연결과 설정

소유자 키와 외부 AI 인증은 별개입니다. **설정 → AI 연결 및 선택**에서 키 저장/런타임 로그인 후 **연결 확인**을 하고 활성 AI를 저장합니다. 미설정·확인 만료·인증 실패 공급자는 선택할 수 없습니다. 탭 간 충돌은 상태 새로고침 후 처리합니다. 인증이 없어도 기존 결과·편집·내보내기·로컬 음성 처리는 가능합니다.

| 공급자 | 준비 방법 |
| --- | --- |
| Codex | `bash scripts/login-codex.sh`로 전용 구독 런타임에 로그인합니다. 기본 인증 위치는 `~/.local/share/local-meeting-minutes/codex-home`입니다. |
| Gemini | 설정에서 키를 저장하고 연결 확인합니다. `<config_dir>/.apikey/gemini.json`만 사용하며 저장 성공은 인증 성공이 아닙니다. [서비스 조건](https://ai.google.dev/gemini-api/terms)을 확인하고 무료 서비스에 기밀·민감정보를 보내지 않습니다. |
| Claude | `python3 scripts/setup-claude.py`로 설치 계획 확인, `--install`로 고정 CLI 설치 후 `.venv/bin/python scripts/login-claude.py`로 전용 구독 로그인합니다. API 키 과금 인증은 사용하지 않습니다. 현재 소유자는 로그인을 보류했습니다. |

모델·한도·실제 검증 상태는 [마일스톤](milestone.md#ai-공급자와-사용-한도)에 있습니다. 설정 변경은 새 요청부터 적용합니다. 등록 당시 설정을 바꿀 수 없는 작업은 취소/전사만 종료 후 저장된 전사에서 새로 생성합니다. 같은 요청의 재시도로 공급자나 예산을 바꾸지 않습니다.

**AI 사용 예산**에서 공급자별 한도를 조정합니다. Codex·Claude는 주간 토큰, Gemini는 일일·분당 요청 단위입니다. 대기 중인 작업은 한도 인상·갱신 후 자동 재개됩니다. 진단의 보관 중 사용 기록은 회의 삭제에 영향을 받지만, 예산 원장은 회의 삭제로 초기화되지 않습니다. 둘 다 계정 전체 청구량·남은 한도와는 다릅니다.

### 진단·백업·서비스

`bash scripts/doctor.sh`는 로컬 필수 환경과 선택적 AI 준비 상태를 구분합니다. 웹 진단은 Codex·Claude 실행 파일·인증 경로·버전·로그인 상태를 30초간 재사용합니다. 로컬 파일/로그인 상태는 실제 원격 생성 성공을 뜻하지 않습니다.

백업 전에 앱을 정지합니다. 백업에는 회의 내용이 포함되지만 인증·로그인 세션은 제외됩니다. 복원은 크기·해시 확인 후 존재하지 않는 새 경로에만 수행하며, 새 환경의 AI는 다시 인증·연결 확인합니다. 별도 백업은 앱 삭제로 지워지지 않습니다.

```bash
bash scripts/stop.sh
bash scripts/backup.sh /absolute/new-backup.tar.gz
bash scripts/restore.sh /absolute/new-backup.tar.gz /absolute/new-data-directory
MINUTES_DATA_DIR=/absolute/new-data-directory bash scripts/run.sh

# 선택적 systemd 사용자 서비스: 출력된 활성화 안내를 따릅니다.
bash scripts/install-service.sh
```

사용자 앱을 갱신할 때는 진행/대기 작업 확인 → 정지·백업 → DB 이전·재시작 → HTTP·산출물·작업 상태 보존 확인 순서를 따릅니다. 실제 계정 로그인이나 불필요한 생성 시험을 갱신에 끼워 넣지 않습니다.

## 개발과 범위 검증

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/local-meeting-minutes/toolchain/env.sh"
uv sync --frozen
(cd apps/web && npm ci)
bash scripts/check.sh fast
bash scripts/check.sh affected tests/test_queue_media.py
# 변경 위험에 필요한 범위만 선택합니다.
bash scripts/check.sh integration
bash scripts/check.sh e2e
bash scripts/check.sh release
```

마스터가 구현·최소 영향 검사 후 커밋으로 고정하고, 검증자는 요청 범위만 확인합니다. 제품 코드·기준 테스트를 검증자가 수정하지 않습니다. 통과 증거는 관련 코드·설정·잠금·fixture 지문이 유효할 때 재사용합니다. 전체 회귀는 릴리즈/넓은 영향에 필요할 때 한 번 수행하고 실패 수정 후에는 해당 범위만 재검증합니다.

`release`는 lint·저비용 Python·TypeScript·mock E2E·증거 무결성을 검사하며 실제 모델·CLI 생성·soak를 자동 실행하지 않습니다. 현재 M3 색인은 `.workflow/evidence/m3/execution-index.json`입니다. 비밀 경계·공급자 계약·snapshot·웹·호환성·운영·설치·범위 검수·실제 회의/영상 생성을 구분합니다. 실패·LIVE_BLOCKED·누락은 전체 인수 PASS가 아니며 mock으로 대체하지 않습니다. 검수 SHA와 불변 결과도 일치해야 합니다.

`.workflow`가 없는 새 checkout이나 오래된 지문을 현재 PASS로 표시하지 않습니다. MVP/M2 증거는 별도 보존하고 관련 변경이 없는 STT/YouTube 증거만 재사용합니다. 이전 번들의 VERIFIED 판정을 새 코드에 승계하지 않습니다. 새 설치 인수는 clean archive SHA·무패치 설치본·파일 해시를 연결합니다.

### 실제 호출과 120분 검사

- 실제 공급자 검사는 허가된 짧은 가상/공개 전사와 명시적인 횟수·시간·토큰 예산으로 진행합니다. 인증·모델 접근 검사와 생성 성공은 구분합니다. Gemini 회의/영상, Claude 연결 등 미완료 항목은 [마일스톤](milestone.md#검증-상태와-남은-인수)을 따릅니다.
- `model`·`live-codex`는 절대 입력 경로·SHA256·언어·권리 정보가 있는 fixture, 입력 manifest SHA에 묶인 영속 예산, 새 결과 경로가 필요합니다. 호출 직전에 예산을 소비하며 실패해도 초기화하지 않습니다. 무거운 실행은 공유 잠금으로 직렬화합니다.
- **120분 soak는 일반·전체·릴리즈 및 완료 조건에서 제외합니다. 새 사용자 직접 지시가 있을 때만 별도 예산과 `--user-requested-soak`를 사용합니다. 과거 승인·누락 증거·일반 실행 허가는 대체 근거가 아닙니다.** 입력은 오디오 트랙 하나의 정확히 7,200초 자료입니다.

```bash
# 사용자가 해당 120분 실행을 명시적으로 지시한 경우에만:
bash scripts/check.sh soak --user-requested-soak \
  --fixture /absolute/fixture.json --budget /absolute/budget.json --output /absolute/new-result
# 기존 증거 해시 확인만 수행하며 실제 실행하지 않습니다.
.venv/bin/python scripts/check-evidence.py --include-soak
```

### AI 변경의 필수 검수 경계

가짜 키·CLI·HTTP로 키 원자 교체/권한/경합/오류 비노출, 소유자·CSRF·revision, 비밀 제외, 세 공급자의 공통 생성·긴 입력·보정·취소를 확인합니다. 작업 설정·checkpoint·재시도 예산을 재시작/정책 변경/키 교체 후에도 보존해야 합니다. 호출 직전 영속 예약·공급자별 잠금·사용량 정규화·미확정 예약·Codex/Claude 완성 초과 결과·Gemini 태평양 날짜/DST·자동 FIFO 재개·원문 보호·회의 삭제 후 예산 보존을 확인합니다. 브라우저 검사는 최신 빌드를 사용합니다.

## 큐 검수와 알림 소비

1. `.workflow/reviews/<id>/request.json`에 고정 `head_commit`·`base_commit`, 검증 worktree, 범위·질문·근거·허용 명령, full/model/live 허용 여부·양수 실행 예산, 결과 경로·회신 UUID를 기록합니다. 요청 ID·검수 중 snapshot은 재사용/변경하지 않습니다.
2. Windows에서 `python scripts/verify_dispatch.py --registry .workflow/session-registry.json --request-id <id>`로 기존 검증자에게 `codex queue` 전송합니다. R1/R2 전체 검증은 금지하며 R3도 명시적 허용이 필요합니다. soak는 별도 사용자 지시가 필요합니다.
3. 검증자는 ID·SHA를 확인하고 한 요청씩 처리합니다. 중복은 조용히 기존 결과를 사용합니다. `result.json`에 한국어로 `PASS/CHANGES_REQUIRED/BLOCKED`, 실제 검사·근거·결함·미검사 범위를 기록합니다.
4. 결과를 불변 저장한 뒤 `VERIFY_RESULT id=<id>; commit=<40자리 SHA>; result=<절대 경로>`를 한 번만 보냅니다. 전송 기록은 `delivery.json`으로 분리합니다. 마스터는 알림이 아니라 결과를 읽고 처리합니다.
5. 판정과 무관하게 처리한 결과의 알림을 아래 명령으로 소비합니다. 알림에는 답장하지 않고 무관한 메시지는 보존합니다. 체크포인트와 턴 종료 전에 지연 알림도 정리합니다.

```powershell
python scripts/consume-review-acks.py --registry .workflow/session-registry.json --accept-result <id>
python scripts/consume-review-acks.py --registry .workflow/session-registry.json
```

읽기 전용 `queue-read.py`로 소비를 대신하지 않습니다. 구형 ACK/FAIL은 동결된 `legacy-review-ids.json`의 ID만 인정합니다. 외부 차단 시 독립 작업을 마친 뒤 정확한 재개 지점을 남깁니다.

## 로컬 상태·비밀·동기화

`.workflow`는 Git 제외 로컬 작업 상태이며 인증 저장소가 아닙니다. `session-registry.json`은 세션·경로, `state.json`은 체크포인트·진행·다음 행동·미완료 리뷰·blockers, `reviews/`는 불변 검수, `evidence/`는 지문·실측, `ack-consumption.json`은 소비 기록입니다. 작업 완료·리뷰 처리·차단·인수인계 때 갱신하며 `plan_sync`에는 마일스톤·`UPDATED/NO_CHANGE/PENDING`·문서 식별자만 둡니다.

Git에는 소스·테스트·잠금·문서·고지만 포함합니다. 데이터·모델·인증·개인 증거·번들은 넣지 않습니다. `.apikey`, CLI 인증, 모든 깊이의 `.gemini_api_key` 및 비밀 임시 파일은 추적 여부와 무관하게 동기화·백업·릴리즈에서 거부/제외합니다. 준비 캐시는 복원하지 않습니다. 키·토큰·인증 출력은 개발 세션·로그·스크린샷·증거에 넣지 않습니다.

```bash
.venv/bin/python scripts/sync-source.py --destination /mnt/d/LocalMinutes
```

프로젝트 소스·문서는 `.gitattributes`·`.editorconfig`로 **LF**, 웹 다운로드만 **UTF-8·CRLF**를 사용합니다. 도구가 편집기 규칙을 무시할 수 있으므로 교차 플랫폼 수정 후 작업 사본과 인덱스를 검사합니다. 바이너리·캐시·개인 자료는 변환하지 않습니다.

```bash
python3 scripts/check-line-endings.py
python3 scripts/check-line-endings.py --index
```

`fast`/`release` 및 GitHub Actions의 LF 검사를 유지합니다. 원격에 반영한 뒤 필수 상태 검사로 지정하면 실패 PR 병합을 막을 수 있지만 디스크 저장 자체를 금지하는 것은 아닙니다.

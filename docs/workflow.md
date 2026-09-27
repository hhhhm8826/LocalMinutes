# 작업·검수 규약

## 문서와 실행 환경

- `README.md`: Git 사용자용 기능·설치·사용·운영 안내. `docs/milestone.md`: 완료 기능·현재 마일스톤·인수인계. 이 문서: 실행·검수·`.workflow` 규칙. `AGENTS.md`: 영문 진입 지침만 유지한다.
- 동작·명령·구조가 바뀌면 관련 문서를 직접 최신화한다. 마일스톤 완료 전에 문서를 동기화하며 상세 변경 이력은 별도로 만들지 않는다. 문서만 바꾸면 추가 리뷰·전체 검증은 필요 없다.
- 현재 실제 원본과 실행 환경은 Ubuntu/WSL의 `/home/cs2023/src/local-meeting-minutes`, Windows 작업 사본은 `D:\LocalMinutes`다. 새 환경에서는 자신의 Linux 경로를 등록한다. 런타임·DB·모델은 Linux 파일시스템에 두고 Windows에서는 브라우저·편집·기존 세션 큐를 사용한다.
- 기존 **Astra medium 마스터 + Sol 5.6 high 검증자** 두 세션만 사용한다. UUID는 개인 `.workflow/session-registry.json`에서 읽고 Git 문서에 넣지 않는다. 추가 세션/재귀 서브에이전트는 만들지 않는다.
- 관리자 설치·로그인·모델 이용조건·권한 변경은 기존 사용자 승인 범위를 먼저 확인한다. 새 환경의 계정 작업은 소유자가 직접 한다. 비밀번호·토큰을 채팅이나 Git에 넣지 않는다. 원격 push·외부 공개는 별도 지시가 필요하다.

## 변경과 검증

마스터가 구현하고 관련 최소 검사 후 커밋으로 고정한다. 검증자는 요청 범위만 확인하며 제품 코드·기준 테스트를 수정하지 않는다. 초기 MVP는 R1(업로드/큐/취소), R2(음성/버전/CLI), R3(릴리즈/전체 요구)로 종료했다. 후속 검증도 해당 위험 범위와 수정 재검증으로 묶고 요청 ID를 재사용하지 않는다.

```bash
bash scripts/check.sh fast
bash scripts/check.sh affected tests/test_queue_media.py
bash scripts/check.sh integration
bash scripts/check.sh e2e
bash scripts/check.sh release
```

`release`는 lint·저비용 Python 회귀·TypeScript·mock 기반 E2E·필수 실행 증거 무결성 검사다. 실제 모델·CLI·120분 처리를 자동 실행하지 않는다. `.workflow`가 없는 새 checkout은 실행 증거 검사를 통과했다고 표시할 수 없다. 부족한 증거를 보고하고 허용된 필요한 범위만 준비한다.

현재 `release`는 M2 증거 프로필과 `.workflow/evidence/m2/execution-index.json`을 검사한다. 한국어·영어 모델 증거의 유효한 재사용, 현재 회의록/영상 실제 생성, 의미 비교, 실제 YouTube 획득, 설치 증거가 모두 필요하다. 과거 MVP 색인은 보존하며 M2 통과로 대체하지 않는다. 누락·차단된 실행은 실패 상태로 남긴다. 증거만 다시 검사할 때는 `python scripts/check-evidence.py --profile m2 --manifest .workflow/evidence/m2/execution-index.json`을 사용한다.

성공한 검사·실제 실행 증거는 관련 코드·설정·잠금·fixture 지문이 유효하면 재사용한다. 전체 회귀는 릴리즈나 넓은 영향에 필요한 때만 수행하고, 실패 수정 후에는 해당 검사와 영향 범위만 재검증한다. mock 결과를 실제 모델·CLI 성공으로 표현하지 않는다. 미완료를 문서에서 지워 완료로 만들지 않는다.

### 선택적 고비용 검사

`model`·`live-codex`에는 명시적 fixture JSON·영속 예산 JSON·새 출력 경로가 필요하다. 예산은 입력 manifest SHA와 최대 횟수·시간을 제한하며 사용 후 초기화하지 않는다. 동일 장비의 무거운 실행은 공유 잠금으로 직렬화한다.

**120분 `soak`는 일반·전체·릴리즈 검증과 완료 조건에서 제외한다. 과거 허가, 기본 모델 예산, 오래되거나 없는 증거는 재실행 근거가 아니다. 사용자가 해당 실행을 명시적으로 지시한 때에만 아래 표시와 별도 예산으로 실행한다. 에이전트가 표시 옵션을 스스로 붙여 승인을 대체하면 안 된다.**

```bash
# 사용자의 명시적 120분 실행 지시가 있을 때만:
bash scripts/check.sh soak --user-requested-soak \
  --fixture /absolute/fixture.json --budget /absolute/budget.json --output /absolute/new-result
# 과거 장시간 증거의 해시만 별도 검사 (실행하지 않음):
.venv/bin/python scripts/check-evidence.py --include-soak
```

`fixture`에는 허가된 절대 입력 경로·SHA256·라이선스·언어가 필요하다. soak 입력은 오디오 트랙 하나의 정확히 7,200초 자료다. 일반 `check-evidence.py`는 soak를 요구하지 않고 기존 soak 항목도 검사에서 제외한다. 제품의 120분 입력 지원과 짧은 경계 단위 테스트는 유지한다.

## 큐 검수와 알림 소비

1. `.workflow/reviews/<id>/request.json`에 고정 `head_commit`, `base_commit`, 검증 worktree, 범위·질문·근거·허용 명령, `allow_full_suite`, `allow_model_run`, `allow_live_codex`, 실행 예산, 결과 경로·회신 UUID를 기록한다. 검증 중 snapshot은 바꾸지 않는다.
2. Windows에서 `python scripts/verify_dispatch.py --registry .workflow/session-registry.json --request-id <id>`로 기존 검증자에게 `codex queue` 전송한다. R1/R2 전체 검증은 금지하며 R3도 명시적 허용이 필요하다. 모델/CLI 허용은 별도 양수 예산이 필요하다. soak는 이 허용과 별개로 사용자 직접 지시가 필요하다.
3. 검증자는 ID·SHA를 확인하고 한 요청씩 처리한다. 중복은 조용히 기존 결과를 재사용한다. 결과는 `PASS/CHANGES_REQUIRED/BLOCKED`, 실제 검사·명령·근거·결함·미검사 범위를 포함한다. 결과 내용은 한국어로 작성한다.
4. `result.json`을 최종 저장한 뒤 `VERIFY_RESULT id=<id>; commit=<40자리 SHA>; result=<절대 경로>`를 마스터에게 한 번 보낸다. 이후 결과를 수정하지 않고 전송 기록은 `delivery.json`에 분리한다. 마스터는 알림이 아니라 결과를 읽어 처리한다.
5. 처리한 결과는 판정과 무관하게 아래 소비 명령으로 해당 알림만 제거한다. 알림에 답장하지 않으며 무관한 메시지는 보존한다.

```powershell
python scripts/consume-review-acks.py --registry .workflow/session-registry.json --accept-result <id>
# 검수 체크포인트와 턴 종료 전 지연 알림 정리:
python scripts/consume-review-acks.py --registry .workflow/session-registry.json
```

읽기 전용 `queue-read.py`로 소비를 대신하지 않는다. 구형 ACK/FAIL은 동결된 `legacy-review-ids.json`의 과거 ID만 인정한다. 반복 상태 메시지 대신 결과 파일·활성 세션을 확인하고, 인증·외부 차단이면 가능한 독립 작업 후 재개 지점을 남긴다.

## `.workflow`와 완료 처리

`.workflow`는 Git 제외 로컬 작업 상태이며 인증 저장소가 아니다. `session-registry.json`은 세션·경로, `state.json`은 진행·재개, `reviews/`는 불변 검수 요청/결과와 별도 전송 기록, `evidence/`는 시험 결과·지문·실측, `ack-consumption.json`은 알림 처리 기록을 보관한다. 기존 결과·예산을 덮어써 새 성공처럼 만들지 않는다.

작업 묶음 완료·리뷰 수신·차단·인수인계 전에 `state.json`의 체크포인트, 진행 작업, 다음 행동, 미완료 리뷰, blockers를 갱신한다. `plan_sync`에는 호환성을 위해 마일스톤·`UPDATED/NO_CHANGE/PENDING`·문서 갱신 식별자만 둔다. 새 환경에서 로컬 상태가 없으면 milestone과 Git 상태에서 재구성하며 과거 PASS를 창작하지 않는다.

완료는 현재 요구에 맞는 구현·검사·증거와 미해결 결함을 대조해 판단한다. Git에는 소스·테스트·잠금·이 네 문서·고지를 포함하고 데이터·모델·인증·개인 증거·번들은 넣지 않는다. 릴리즈는 clean 커밋 바이트와 빌드 자산으로 만들고 필수 문서·비밀 제외·파일 해시를 확인한다. 필요한 설치 검증은 실제 archive SHA와 무패치 설치본을 연결한다. 기존 VERIFIED 번들의 판정을 이후 변경 전체에 자동 승계하지 않는다.

## 줄바꿈

프로젝트 텍스트는 LF로 저장한다. `.gitattributes`의 `* text=auto eol=lf`는 Git 저장·체크아웃을 LF로 통일하고, `.editorconfig`는 지원 편집기의 저장 형식을 맞춘다. `Set-Content` 등 도구가 이를 무시할 수 있으므로 작업 사본과 인덱스를 별도로 검사한다.

```bash
python3 scripts/check-line-endings.py
python3 scripts/check-line-endings.py --index
```

검사는 추적 파일과 Git 제외 대상이 아닌 새 텍스트를 대상으로 하며 바이너리·개인 `.workflow`·의존성·캐시는 변환하지 않는다. `fast`/`release`에 작업 사본 검사를 포함한다. GitHub Actions의 `LF line endings`는 작업 사본과 인덱스를 검사한다. 원격에 반영한 뒤 이를 필수 상태 검사로 지정하면 실패한 PR 병합도 차단할 수 있다. 편집기 설정이나 Git 정규화가 디스크상의 CRLF 저장 자체를 금지하는 것은 아니다.

웹에서 다운로드하는 텍스트·Markdown 파일은 Windows 사용을 위해 UTF-8·CRLF로 제공한다. 저장소 소스·문서는 기존 LF 규칙을 유지한다.

# 사전작업: 개발 환경·권한·두 세션 준비

문서 버전: 1.0 | 작성일: 2026-09-27 | 실행 순서: 이 문서 → `01_MVP_GOAL_PLAN.md`

> 이 문서는 MVP 구현 전에 **실제로 실행할 준비 절차**다. 설치되었다고 추측하거나 체크박스만 채우지 않는다. 필요한 설치·로그인·승인을 앞에서 모아 처리하고, 최종 개발 권한으로 시험한 뒤 `/goal`을 시작한다. 문서 제공 시점에 사용자의 PC에서 준비나 성능 검증을 수행한 것은 아니다.

## 0. 시작 방법과 역할

두 세션은 대화 이름이 아니라 **고유 세션 UUID**로 연결한다. `Astra medium`, `Sol 5.6 High`는 사용자가 지정한 역할·모델 표시명이다. CLI 모델 ID를 이 문자열에서 만들어내지 말고 실제 모델 목록과 `/status`로 확인한다. 사용자의 지정 모델을 찾지 못하면 다른 모델로 몰래 바꾸지 않는다.

**개발 세션은 두 개뿐이다.** 제품이 회의록 생성을 위해 실행하는 일회성 `codex exec` 프로세스는 개발 검증 세션이 아니며, 두 개발 세션을 재사용해서도 안 된다.

## 1. 이번 구현에서 바꾸지 않을 결정

| 구분 | 고정 방향 |
|---|---|
| 사용 형태 | 개인 소유 장비에서 쓰는 로컬 웹 앱, 기본 주소는 루프백 |
| 개발 PC | Windows 호스트 + WSL2 Ubuntu 24.04 LTS x86_64 |
| 릴리즈 대상 | Ubuntu 24.04 LTS x86_64, CPU 전용 기본 구성 |
| 하드웨어 가정 | Intel 12세대 또는 AMD Zen4 이상, 물리 6코어 이상, 호스트 RAM 32GB |
| 음성 처리 | WhisperX + faster-whisper/CTranslate2 `large-v3-turbo`, CPU `int8` |
| 화자 구분 | 로컬 pyannote `speaker-diarization-community-1` |
| LLM | 개인 구독으로 로그인한 **Codex CLI만** |
| 언어·입력 | 한국어·영어·혼용, 음성·영상 파일, 영상은 음성만 사용 |
| 제한 | 파일 1개 최대 2GB(2,000,000,000바이트), 재생 시간 최대 7,200초, 실제 발화자 최대 12명 |
| 처리 | 영속 FIFO, 활성 작업 최대 1개, 웹 상태·취소·재시도 |
| 기본 스택 | React·TypeScript·Vite / FastAPI·Pydantic / SQLite·로컬 파일 |
| 제외 | Gemini 및 다른 LLM API, 유료 API 폴백, 실시간 STT, SaaS·다중 사용자, 외부 문서 발행 |

Windows 네이티브 Python·PyTorch까지 동시에 지원하는 것을 MVP 목표로 삼지 않는다. Windows에서는 브라우저·편집기를 사용하고 실제 Node/Python/Codex 개발 실행은 같은 WSL 배포판 안에서 수행한다. Windows에서 고른 파일은 브라우저 업로드로 전달하며 Windows 파일 경로를 서버에 실행 인자로 넘기지 않는다.

## 2. 실행 전 조사와 일괄 승인

아직 설치하거나 시스템 설정을 바꾸지 말고 다음을 먼저 조사한다.

- Windows 버전, WSL 설치·버전·배포판, 가상화 지원, 재부팅 필요 여부.
- 호스트의 물리 코어·논리 프로세서·RAM과 WSL 내부의 실제 CPU·RAM·스왑.
- 저장소 경로·Git 상태·기존 코드와 설정, 디스크 여유, 다른 WSL 작업의 존재.
- 두 개발 세션의 CLI 버전, 로그인 방식, 사용 가능한 모델, 현재 권한.
- FFmpeg, Python, uv, Node, Git, 모델 캐시, 공개 테스트 파일의 기존 설치 여부.

마스터는 **관리자 설치, 브라우저 로그인, Hugging Face 사용조건 동의, 프로젝트 경로 접근, 네트워크·Git 작업 권한**을 하나의 사전 승인 목록으로 정리한다. 사용자 비밀번호·토큰을 채팅으로 요구하거나 직접 읽지 않는다.

일반 의존성 추가와 공식 문서 검색은 이 프로젝트의 승인 범위에 포함하되, 유료 결제, 새 계정 생성, 외부 공개, 다른 프로젝트 삭제, 전역 보안 약화는 포함하지 않는다. 이미 받은 답변을 다시 묻지 않는다.

관리자 승인, 2단계 인증, 재부팅, 사용량 소진, 조직 정책은 문서나 에이전트가 없앨 수 없다. 해당 항목은 `BLOCKED`로 명시하고, 가능한 독립 준비 작업을 먼저 수행한 후 필요한 사용자 조치를 한 번에 안내한다. 권한 거부를 우회하지 않는다.

## 3. Windows와 Ubuntu 환경 준비

### 3.1 WSL 설치·확인

아래는 **Windows PowerShell**에서 실행하는 명령이다. 설치가 없을 때만 관리자 승인을 받아 설치한다. 배포판 이름은 먼저 실제 목록으로 확인한다. [S1]

```powershell
wsl --status
wsl --version
wsl --list --verbose
wsl --list --online
# Ubuntu-24.04가 미설치인 경우에만:
wsl --install -d Ubuntu-24.04
```

설치·재부팅은 장시간 `/goal` 전에 끝낸다. 마스터가 임의로 `wsl --shutdown`을 실행해 두 세션과 다른 작업을 종료하지 않는다.

두 세션과 저장소는 같은 WSL 배포판·Linux 사용자에서 실행한다. 저장소·SQLite·모델 캐시는 `/mnt/c`나 OneDrive가 아닌 Linux 파일시스템에 둔다. 예: `~/src/local-meeting-minutes`. Windows에서는 VS Code의 WSL 연결과 `\\wsl.localhost\Ubuntu-24.04\...`로 접근한다. [S2]

### 3.2 WSL 자원

호스트 32GB가 WSL에 그대로 할당되는 것은 아니다. `.wslconfig`의 기본 메모리는 호스트의 50%이므로 실제 할당을 확인한다. [S3]

이 프로젝트의 **초기 권장 상한**은 WSL RAM 24GB, 스왑 4GB다. Windows에 여유를 남기는 시작값이며 처리 성능 보장은 아니다. 기존 `.wslconfig`를 백업하고 다른 배포판 영향까지 사용자와 확인한 후 필요한 값만 수정한다. 논리 CPU 수를 물리 코어 수로 오인하지 않는다.

```ini
[wsl2]
memory=24GB
swap=4GB
```

기존 `processors` 설정은 실제 장비를 확인해 유지·조정한다. 애플리케이션 추론 스레드는 우선 4개, 배치 크기는 1로 시작한다. 두 세션이 동시에 무거운 추론을 실행하지 않도록 공용 테스트 잠금도 준비한다.

### 3.3 Linux 기본 패키지와 개발 도구

Ubuntu 24.04에서 필요한 시스템 패키지를 정확히 열거한 설치 스크립트를 만든다. 기본 후보는 `ffmpeg`, `git`, `ca-certificates`, `curl`, `build-essential`, Python 가상환경 관련 패키지다. ML 휠과 브라우저 실행에 필요한 추가 라이브러리는 실제 오류·공식 요구사항을 근거로 추가한다.

- Python은 3.12 계열을 기준으로 패치 버전을 기록한다.
- Node는 24 LTS 계열을 기준으로 `.node-version` 등에 고정한다. [S4]
- Python은 uv 잠금 파일, 프런트엔드는 npm과 `package-lock.json`을 사용한다.
- 런타임·ML·개발 의존성을 구분하고, 빠른 테스트 때문에 모델 패키지를 반복 설치하지 않는다.
- PyTorch·torchaudio·torchcodec·WhisperX의 **호환 조합 전체**를 확인하고 고정한다.
- CPU 휠 인덱스를 명시적으로 선택한다. GPU가 없는 PC에 CUDA 구성부터 설치하지 않는다. uv의 공식 PyTorch 인덱스 설정을 따른다. [S5]
- Git `main`, 프리릴리즈, `latest` 추적을 릴리즈 설치 기준으로 남기지 않는다. 안정 버전을 한 번 선택한 후 잠근다.
- 임의의 원격 설치 스크립트를 읽지 않은 채 관리자 권한으로 실행하지 않는다. 공식 배포처·체크섬을 우선 사용한다.

WhisperX의 Python·Torch 의존성은 바뀔 수 있다. 작성 당시 저장소 메타데이터를 참고하되, 준비 시점의 **안정 릴리즈**를 기준으로 해결한다. 문서에 적힌 조합을 실제 설치 성공으로 간주하지 않는다. [S6]

## 4. 권한을 실제 작업으로 검증

### 4.1 권한 범위

| 주체 | 필요한 권한 | 부여하지 않을 권한 |
|---|---|---|
| 마스터 | 제품 저장소 수정, 로컬 커밋·검증 worktree 준비, 승인된 패키지 설치·캐시, 로컬 서버·테스트, 공식 문서 검색, 지정 세션으로 큐 전송 | 임의 전역 관리자 권한, 다른 프로젝트 수정, 무단 원격 push·공개 릴리즈 |
| 검증 세션 | 지정 커밋 읽기, 독립 worktree에서 테스트, 승인된 캐시 이용, 자기 결과·임시 파일 작성, 공식 문서 검색, 마스터로 큐 회신 | 제품 코드·기준 테스트 수정, 새 기능 개발, 임의 의존성 변경, 추가 개발 세션 생성 |
| 제품의 Codex 호출 | 지정 텍스트 입력, 로그인된 CLI의 모델 통신, 회의록 결과 출력 | 개발 세션 조작, 임의 명령 실행, 외부 웹·MCP·플러그인·개발 지침 사용 |

검증 세션의 “코드 읽기 전용”은 테스트 임시 파일까지 전부 금지한다는 뜻이 아니다. 별도 검증 worktree·보고서·캐시·임시 디렉터리에 필요한 쓰기만 허용하고 원본 코드 변경은 금지한다.

### 4.2 네트워크와 승인 정책

개발 세션에는 다음 연결을 준비한다: 공식 문서 검색, GitHub의 공식 프로젝트, Ubuntu 패키지 저장소, PyPI·Python 배포처, npm·Node 배포처, PyTorch 휠, Hugging Face와 모델 다운로드 CDN, Codex 인증·서비스, 브라우저 테스트 배포처. 샘플 확보에 필요한 YouTube 및 허용된 원본 배포처는 별도로 기록한다.

브라우저 검색 도구와 셸에서의 HTTP 연결은 서로 다르므로 **둘 다 실제 시험**한다. 도메인 제한 기능을 쓸 때는 CDN 리다이렉트와 실제 정책 활성화 여부까지 확인한다. TLS 검증을 끄거나 방화벽을 전역 해제하지 않는다.

준비 중에는 승인을 받을 수 있는 모드로 정확한 범위를 설정한다. 준비가 끝나면 승인된 경계 안에서 반복 프롬프트 없이 동작하는 프로필을 적용한다. 공식 CLI의 예시 설정은 아래와 같지만 설치된 버전의 권한 프로필과 상위 정책을 우선 확인한다. [S7]

```toml
# 승인 범위가 확정된 개발용 설정 예시. 제품의 Codex 실행 설정과 구분한다.
approval_policy = "never"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
```

`never`는 **권한을 주는 기능이 아니라 추가 승인 질문을 하지 않는 설정**이다. `.git`, Codex 세션 저장소, worktree의 공용 Git 디렉터리, 외부 캐시는 추가 제약이 있을 수 있다. 허용된 Git·큐 작업과 필요한 경로 예외를 실제로 승인·시험한다. `codex queue`를 실행할 수 있도록 무조건 전체 홈 디렉터리 쓰기를 열지 않는다.

`danger-full-access`, `--yolo`, 상위 정책 우회, 광범위한 `sudo`·`bash`·`python` 무조건 승인으로 문제를 덮지 않는다. **최종 프로필에서 Git 체크포인트·패키지 설치·검색·큐 왕복·서버 시작이 모두 성공해야 한다.** 안 되는 항목은 READY가 아니다.

## 5. Codex CLI와 두 개발 세션 연결

### 5.1 실제 기능·모델 확인

같은 WSL 환경에서 다음을 확인하고 명령 결과 중 비밀이 없는 부분만 기록한다.

```bash
codex --version
codex --help
codex queue --help
codex exec --help
codex login status
```

공식 CLI는 `/goal <목표>` 및 파일을 참조하는 긴 지시 방식을 제공한다. `codex queue`는 기존 세션으로 메시지를 보내는 기능이다. 설치 버전에 두 기능이 모두 있는지 확인하고, 필요하면 사용자 승인하에 안정 버전으로 갱신한다. 버전 번호만 보고 지원을 추측하지 않는다. [S8][S9]

두 세션에서 각각 `/model`, `/status`로 지정된 모델·추론 설정을 확인한다. 지원되는 경우 `codex debug models`도 활용한다. 세션 이름은 예를 들어 `minutes-master`, `minutes-verifier`로 두되 실제 전송에는 UUID를 쓴다.

### 5.2 저장소와 상태 파일

마스터는 다음 경로를 준비한다. `.workflow/`, `data/`, 비밀 설정, 모델 캐시는 Git에서 제외한다.

```text
프로젝트/
  AGENTS.md
  docs/00_PREFLIGHT.md
  docs/01_MVP_GOAL_PLAN.md
  .workflow/
    preflight.json
    session-registry.json
    state.json
    reviews/
    evidence/
  scripts/                       # 준비·구현 과정에서 작성
```

검증용 worktree는 저장소 옆의 별도 Linux 경로에 만든다. 마스터만 리뷰 요청 직전 검증 worktree를 지정 커밋으로 맞춘다. 검증 진행 중에는 그 worktree를 수정하거나 전환하지 않는다. 리뷰가 끝나기 전에 마스터는 자신의 원본 worktree에서 독립 작업을 계속할 수 있다.

`.workflow/session-registry.json`에는 실제 값을 기록한다.

```json
{
  "schema_version": 1,
  "master": {
    "thread_id": "실제 UUID",
    "model_id": "실제 모델 ID",
    "reasoning_effort": "medium",
    "worktree": "실제 절대 경로"
  },
  "verifier": {
    "thread_id": "실제 UUID",
    "model_id": "실제 모델 ID",
    "reasoning_effort": "high",
    "worktree": "실제 절대 경로"
  },
  "shared_workflow_root": "실제 절대 경로",
  "development_codex_home": "실제 경로",
  "queue_roundtrip_verified": false
}
```

위 값은 형식 예시다. `실제 UUID` 같은 문자열이 남은 상태는 준비 완료가 아니다. 두 개발 세션은 동일한 Codex 세션 저장소를 볼 수 있어야 하며, Windows와 WSL의 로그인·세션 저장소를 혼용하지 않는다.

### 5.3 실제 큐 왕복 시험

정확한 인자는 설치된 `--help`로 확인한다. 공식 구현의 기본 형식은 아래와 같다. 존재하지 않는 `--message-file` 같은 옵션을 만들어 쓰지 않는다. [S10]

```bash
codex queue --thread "$VERIFIER_THREAD_ID" \
  --message "PREFLIGHT_PING id=setup-001; registry=$REGISTRY_ABS_PATH; 지정 경로와 자신의 역할을 확인하고 마스터 UUID로 한 번만 회신하라."
```

검증 세션은 자신의 준비 결과를 파일에 쓰고 다음과 같이 회신한다.

```bash
codex queue --thread "$MASTER_THREAD_ID" \
  --message "PREFLIGHT_ACK id=setup-001; result=$RESULT_ABS_PATH"
```

최종 승인 정책 상태에서 다음을 확인한다.

1. 대상 세션의 UUID·역할·모델·작업 경로가 정확하다.
2. 유휴 검증 세션이 메시지를 받아 처리하고 마스터가 회신을 받는다.
3. 결과 파일을 양쪽에서 읽을 수 있고 검증 세션은 원본 제품 코드를 수정하지 않는다.
4. 같은 요청 ID가 중복 도착해도 새 작업을 반복 생성하지 않는다.
5. 알림에 회의 원문·토큰·개인 파일을 붙이지 않고 ID·커밋·경로만 전달한다.

큐 명령의 종료 코드 0은 검증 성공이 아니다. **대상 세션의 결과 파일과 회신까지 확인**해야 한다. 검증 세션의 종료·CLI 오류·재로그인이 필요한 경우는 준비 단계에서 해결한다. 마스터가 고빈도 모델 호출로 계속 확인하지 않도록 한다.

## 6. 제품 전용 Codex 인증·격리

### 6.1 개발용 계정 설정과 제품 런타임 설정 분리

제품은 다른 장비에서도 그 장비 소유자가 직접 Codex에 로그인하여 사용하도록 한다. 개발자 로그인 파일을 릴리즈에 넣거나 복사해 배포하지 않는다.

런타임용 `CODEX_HOME`은 저장소 밖의 사용자 전용 디렉터리에 둔다. 예: `~/.local/share/local-meeting-minutes/codex-home`. 두 개발 세션의 `CODEX_HOME`과 분리하고, 별도 홈에서도 로그인·토큰 갱신이 되는지 시험한다. 소유자 전용 접근권한을 설정하고 토큰 내용은 읽거나 로그에 남기지 않는다. [S11]

제품 실행 프로세스에는 API 키 인증을 위한 환경변수를 전달하지 않는다. 설정에서 지원하면 ChatGPT 로그인 방식을 강제하고 실제 인증 상태도 확인한다. 구독 한도 초과 시 유료 API나 다른 모델 서비스로 자동 전환하지 않는다.

### 6.2 사전 비대화형 호출

원격으로 보낼 수 있는 짧은 **가상 회의 텍스트**와 준비용 최소 JSON Schema를 만들어 실제 호출을 한 번 수행한다. G1의 완성된 제품 API나 스키마 구현을 선행 조건으로 요구하지 않는다. 아직 실제 회사 회의나 개인정보를 보내지 않는다.

```bash
# 모든 경로·모델·설정은 설치 환경에서 확인한 값으로 구성한다.
# 런타임 홈의 로그인은 소유자가 별도로 완료해야 한다.
CODEX_HOME="$RUNTIME_CODEX_HOME" codex --ask-for-approval never exec \
  --sandbox read-only \
  --ephemeral \
  --skip-git-repo-check \
  --model "$MINUTES_MODEL_ID" \
  --output-schema "$SCHEMA_ABS_PATH" \
  --output-last-message "$OUTPUT_ABS_PATH" \
  - < "$PROMPT_ABS_PATH"
```

이는 기본 호출 형식이며 완성된 보안 설정 전체가 아니다. 공식 비대화형 실행과 구조화 출력 옵션을 사용하되, 실제 버전에서 동작을 검증한다. `--ephemeral`은 로컬 대화 기록에 관한 옵션이지 서버 무보관을 보장하지 않는다. [S8][S12]

런타임의 작업 디렉터리는 개발 저장소 밖의 작업별 임시 디렉터리다. 부모 경로에 개발용 `AGENTS.md`가 없어야 한다. 개발용 hooks·MCP·plugins·웹 검색·shell 실행 도구를 비활성화하거나 공식 권한 설정으로 동등한 제한을 강제한다. **프롬프트에 “실행하지 마”라고 쓰는 것만으로 격리가 됐다고 보지 않는다.** 실제 활성 설정과 무해한 탈출 유도 입력으로 시험한다.

제품 LLM 모델은 계정에서 확인한 모델 ID 하나를 설정으로 기록한다. 기본 후보는 마스터가 사용하는 Astra medium으로 하되, 개발 세션 ID나 대화 이력을 재사용하지 않는다. 다른 모델 선택은 사용자 선택 또는 명시적인 설정 변경으로만 가능하다.

확인할 항목: 종료 코드, 스키마 검증, 한글 출력, 원문 근거 ID, 로그 비밀 제거, 무인 종료, 타임아웃, 자식 프로세스 종료. 정상 여부 확인에 실사용 구독 한도가 소모됨을 기록한다. 한도 소진·로그인 실패는 별도 상태로 구분한다.

## 7. CPU 모델 전체 캐시와 짧은 시험

### 7.1 다운로드할 대상

실제로 선택한 버전이 참조하는 ASR 가중치, VAD, 토크나이저, 한국어·영어 정렬 모델, pyannote 파이프라인과 하위 모델을 모두 준비한다. `large-v3-turbo` 별칭이 가리키는 **CTranslate2 호환 저장소와 리비전**을 기록한다. 원래 Whisper 체크포인트 경로를 변환된 모델처럼 잘못 전달하지 않는다.

Hugging Face의 필요한 모델 사용조건은 사용자가 확인·동의하고 최소 읽기 권한으로 다운로드한다. 인증 토큰을 저장소·설치 명령 기록·리포트에 남기지 않는다. pyannote는 초기 준비 후 로컬 경로에서 실행하도록 구성한다. [S13]

`device=cpu`, `compute_type=int8`, `batch_size=1`, 추론 스레드 4를 실제 로그·설정에서 확인한다. INT8은 ASR 엔진 설정이며 pyannote·정렬 모델 전체가 INT8이라는 의미가 아니다. [S6][S14]

### 7.2 실제 시험과 통신 차단 확인

권한이 확보된 짧은 음성으로 전사·정렬·화자 구분을 최소 한 번 수행한다. 한·영 모델이 둘 다 로딩되는지도 확인한다. 두 번째 실행은 모델 다운로드 없이 돌아가는지 확인하고, 로컬 음성 단계에서 외부 연결이 필요하지 않음을 별도 네트워크 차단 시험으로 확인한다.

사용량 측정 등 선택적 원격 수집은 끈다. `PYANNOTE_METRICS_ENABLED=0` 등 적용한 설정은 버전별로 실제 유효성을 확인한다. 끄지 못했거나 검증하지 못한 통신은 “완전 로컬”로 표시하지 않는다.

기록할 항목: 모델 로딩 시간, 단계별 처리 시간, 최대 프로세스 트리 RSS, 스왑 사용, 실제 장치·연산 방식, 캐시 재사용. 최초 로딩과 순수 추론 시간을 구분하고 3분 수치로 2시간 성능을 보장하지 않는다.

## 8. YouTube 기반 테스트 샘플 준비

첫 제품 평가에는 150~210초 길이의 샘플을 우선 사용한다. 최소 한국어·영어 각각 하나를 확보하고, 가능한 한 실제 발화자 3~4명이 교대로 말하는 구간을 선택한다. 겹친 발화·짧은 맞장구가 있는 별도 샘플도 확보한다.

- YouTube에서 실제 후보와 구간을 조사한다. 링크만 찾고 확보 완료로 표시하지 않는다.
- 공개 영상이라는 이유로 다운로드·재배포를 허용된 것으로 간주하지 않는다. CC BY, 제작자의 명시 허락, 제공된 원본 다운로드 등 이용 조건과 획득 경로를 확인한다. [S15]
- 접근 제한·DRM을 우회하거나 개인 쿠키·계정 자격 증명을 가져오지 않는다.
- 앱에는 YouTube URL 다운로드 기능을 구현하지 않는다. 확보한 파일을 일반 업로드로 시험한다.
- 영상 1개에서 같은 내용의 WAV를 만들어 영상/음성 입력 경로 비교에 쓴다. 이를 서로 다른 품질 평가 샘플 두 개로 세지 않는다.
- 파일은 Git에 넣지 않고 SHA-256, 원본 주소, 구간, 라이선스, 언어, 실제 확인된 화자 수, 파일 경로를 `data/fixtures/manifest.json`에 기록한다. 배포 가능한 메타데이터만 별도 추출한다.
- 정답 전사·화자 라벨의 출처와 확인 수준을 적는다. 자동 STT 결과를 자기 자신의 정답으로 사용하지 않는다. 확인되지 않은 정답 기반 지표는 미측정으로 둔다.

허가된 자료가 확보되지 않으면 미디어 기능의 실제 검증은 `BLOCKED`다. 합성·가상 입력으로 계약 테스트를 계속할 수 있지만 실제 YouTube 샘플 검증을 대체했다고 선언하지 않는다.

## 9. 준비 중 만들 최소 스크립트

아래 파일은 문서가 아니라 **구현해야 할 준비 도구의 계약**이다. 현재 존재한다고 가정하지 않는다. 같은 기능을 하는 기존 스크립트가 있으면 재사용한다.

| 경로 | 동작 |
|---|---|
| `scripts/bootstrap-ubuntu.sh` | 비관리자 영역 설치·잠금 파일 동기화, 필요한 시스템 설치만 분리 안내 |
| `scripts/dev-windows.ps1` | 지정 WSL 배포판·Linux 저장소로 진입, 경로·공백·인자 처리 |
| `scripts/doctor.py` | 권한·도구·모델·네트워크·인증 진단, 비밀 없는 JSON 출력 |
| `scripts/prepare-models.py` | 선택 모델과 하위 모델 다운로드·리비전 manifest·오프라인 검사 |
| `scripts/verify_dispatch.py` | 고정 UUID로 `codex queue` 전송, 요청 ID·경로 검증·중복 방지 |
| `scripts/smoke-codex.py` | 가상 입력으로 스키마·종료·권한 확인, 테스트 예산 적용 |

모든 스크립트는 재실행 가능하고 기존 환경·사용자 데이터를 덮어쓰지 않아야 한다. 준비용 네트워크 검사를 매 테스트마다 반복하지 않는다. 계획·리포트를 불필요하게 여러 MD로 쪼개지 않고 상태·측정값은 JSON/JSONL로 저장한다.

## 10. READY 판정

`.workflow/preflight.json`에 `checked_at`, 실제 도구·모델 버전, 잠금 파일 해시, 장비·WSL 자원, 검사별 `PASS/FAIL/BLOCKED/NOT_RUN`, 근거 경로를 기록한다. 토큰과 원문은 제외한다.

아래가 모두 참이어야 `status: READY`다.

- [ ] Windows → WSL Ubuntu 24.04 진입, Linux 파일시스템의 저장소와 가상환경 사용이 확인됐다.
- [ ] Python·Node·FFmpeg·ML CPU 호환 조합이 설치·고정됐다.
- [ ] 마스터와 검증 세션의 실제 모델·추론 설정·UUID·역할이 확인됐다.
- [ ] 최종 권한으로 파일·Git 체크포인트·테스트·공식 검색·네트워크·큐 왕복이 통과했다.
- [ ] 검증 worktree·공유 리포트·중복 방지 규약이 동작한다.
- [ ] 런타임 Codex 홈에 개인 구독 로그인이 준비됐고 무인 구조화 출력이 성공했다.
- [ ] 런타임 호출이 개발 세션·지침·도구·API 키를 상속하지 않는지 검사했다.
- [ ] 한국어·영어 모델·정렬·pyannote와 모든 하위 가중치를 캐시했다.
- [ ] 실제 짧은 음성에서 CPU INT8 전사·정렬·화자 구분과 오프라인 재실행이 성공했다.
- [ ] 권한이 확인된 한국어·영어 3분 내외 샘플을 실제 파일로 확보했다.
- [ ] 임시 진단 HTTP 서버에 Windows 브라우저로 접근할 수 있고, 외부 네트워크 공개는 하지 않았다. 제품 API는 G1에서 구현한다.
- [ ] 남은 사용자 조치가 없고 `.workflow/state.json`에 다음 단계 `G1`이 기록됐다.

검증 세션에는 이 준비 결과 중 **큐 수신, 권한 경계, 증거 일관성**만 한 번 확인하도록 요청한다. 설치와 모델 시험 전체를 중복 실행시키지 않는다.

READY 후 마스터는 변경한 설정, 로그인 위치, 사용한 버전, 측정된 짧은 처리 시간, 남은 장시간 품질 검증을 요약한다. `/goal` 입력은 다음 문서 0장에 있다. 이 단계에서는 재부팅·로그인·권한 승인을 남겨둔 채 무인 실행 가능하다고 약속하지 않는다.

## 11. 중단 후 재개

새 세션을 추가하기보다 기존 마스터·검증 세션을 재개한다. UUID가 바뀌면 레지스트리를 바꾸고 큐 왕복만 다시 시험한다. 정상 모델을 다시 내려받지 않는다.

환경·권한·CLI·의존성·계정이 바뀐 부분만 재검사한다. 마지막 실패 단계와 그 의존 단계만 다시 수행한다. 디스크 정리 시 소유권과 참조 관계를 확인하고, 기존 녹음·결과·사용자 설정을 지우지 않는다.

## 12. 공식 근거와 확인 위치

아래는 기능 확인을 위한 공식 출처다. 설치 시점의 `--help`·안정 릴리즈·실제 시험을 최종 실행 기준으로 사용한다. 프로젝트의 설계 선택과 처리 속도 목표는 이 출처가 보장하는 사실이 아니다.

- [S1] Microsoft WSL 설치: https://learn.microsoft.com/en-us/windows/wsl/install
- [S2] OpenAI WSL 개발 안내: https://learn.chatgpt.com/docs/windows/wsl
- [S3] Microsoft WSL 자원 설정: https://learn.microsoft.com/en-us/windows/wsl/wsl-config
- [S4] Node.js 릴리즈: https://nodejs.org/en/about/previous-releases
- [S5] uv의 PyTorch 인덱스 구성: https://docs.astral.sh/uv/guides/integration/pytorch/
- [S6] WhisperX 및 의존성: https://github.com/m-bain/whisperX , https://github.com/m-bain/whisperX/blob/main/pyproject.toml
- [S7] OpenAI 권한·네트워크·승인: https://learn.chatgpt.com/docs/agent-approvals-security
- [S8] OpenAI CLI·`/goal` 명령: https://learn.chatgpt.com/docs/developer-commands?surface=cli
- [S9] `codex queue` 도입 릴리즈: https://github.com/openai/codex/releases/tag/rust-v0.149.0
- [S10] 큐 명령 공식 구현: https://github.com/openai/codex/blob/rust-v0.149.0/codex-rs/cli/src/queue_cmd.rs
- [S11] OpenAI 인증·자격 증명: https://learn.chatgpt.com/docs/auth
- [S12] OpenAI 비대화형·구조화 출력: https://learn.chatgpt.com/docs/non-interactive-mode
- [S13] pyannote community-1 모델 카드: https://huggingface.co/pyannote/speaker-diarization-community-1
- [S14] faster-whisper CPU INT8: https://github.com/SYSTRAN/faster-whisper
- [S15] YouTube 라이선스: https://support.google.com/youtube/answer/2797468?hl=ko

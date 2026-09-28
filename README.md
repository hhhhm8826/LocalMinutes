# 로컬 회의록

**음성·영상에서 논제 중심의 한글 회의록과 영상 요약을 만드는 로컬 웹 앱입니다.**

Ubuntu 또는 Windows의 WSL Ubuntu에서 실행합니다. 전사·화자 구분은 PC에서 처리하고, 요약에 필요한 텍스트와 메타데이터만 선택한 AI(Codex CLI·Gemini API·Claude CLI)로 전송합니다.

## 주요 기능

- 회의록 생성·편집·버전 저장, 제목·본문 검색
- 영상 파일·YouTube 링크 요약, 주제별 근거 음성 재생
- 언어·화자 자동 인식, 처리 대기열·취소·재시도
- 음성 재분석 없는 AI 요약 재생성
- Markdown·텍스트 내보내기, 보관 기간 설정·백업·복원

MVP·M2는 완료됐으며, AI 공급자 확장(M3)은 구현·범위 검수를 마치고 일부 실제 실행 인수를 남겨 두고 있습니다. 자세한 상태는 [마일스톤](docs/milestone.md)을 참고해 주세요.

## 설치 및 실행

Ubuntu 24.04 LTS x86_64와 **Linux 파일시스템의 설치 경로**를 사용합니다. 준비 기준은 RAM 32GB·CPU 6코어 이상입니다. 입력은 파일당 최대 2GB·120분이며, 화자는 최대 12명까지 자동 인식합니다.

저장소를 체크아웃하거나 번들을 푼 뒤 일반 사용자로 실행하세요.

```bash
# 관리자 패키지 설치 안내를 확인하고 안내된 명령을 실행합니다.
bash scripts/install-ubuntu.sh --system-plan
bash scripts/install-ubuntu.sh

# 모델 이용조건 동의 후 로그인하고 모델을 준비합니다.
.venv/bin/hf auth login
bash scripts/prepare-models.sh --accept-model-terms

# 기본 AI인 Codex의 전용 런타임에 로그인합니다.
bash scripts/login-codex.sh
bash scripts/doctor.sh
bash scripts/run.sh
```

모델 준비 전 [pyannote 이용조건](https://huggingface.co/pyannote/speaker-diarization-community-1)에 직접 동의해야 합니다. 위 옵션은 웹 동의나 로그인을 대신하지 않습니다.

브라우저에서 **[http://127.0.0.1:8765](http://127.0.0.1:8765)**를 여세요. 일반 기능은 키 없이 사용하고, 설정·진단은 `~/.config/local-meeting-minutes/owner-key`로 로그인합니다. 키·토큰은 Git이나 채팅에 넣지 마세요.

Windows 실행, Gemini·Claude 연결, 경로 변경과 운영 명령은 [설정·운영 안내](docs/workflow.md#설정과-운영)에 있습니다.

## 사용 방법

1. **새 회의**, **새 영상 요약** 또는 **YouTube 영상 요약**에서 자료를 등록합니다. 제목을 비우면 파일은 AI가, YouTube는 원본 제목으로 채웁니다.
2. 처리가 끝나면 요약과 주제를 확인하고, 시작 시각을 눌러 근거 음성을 들을 수 있습니다.
3. 회의록은 **회의록 편집 → 수정본 저장**으로 새 버전을 저장합니다. 영상 요약은 읽기 전용입니다.
4. **초안 재생성 / 요약 재생성**은 저장된 전사로 AI 요약만 다시 실행합니다.
5. **텍스트 내보내기 / Markdown 내보내기**로 현재 결과를 다운로드합니다. 파일은 UTF-8·CRLF이며 재생 시작 시각은 제외합니다.

설정에서 사용할 AI와 한도를 선택할 수 있습니다. 기본 한도는 Codex·Claude 각각 주간 2,000만 토큰, Gemini는 하루 10회·분당 2회입니다. Gemini 수치는 무료 한도 보장값이 아닌 추정 기준입니다. [공급자·예산 동작](docs/milestone.md#ai-공급자와-사용-한도)을 확인해 주세요.

원본 음성·영상은 기본 7일, 전사·임베딩은 30일 보관하며 결과는 영구 보존합니다. 사용할 권한이 있는 자료를 등록하고, 생성 결과의 정확성은 검토해 주세요.

## 문서

| 문서 | 내용 |
| --- | --- |
| [마일스톤](docs/milestone.md) | 구현 현황, 현재 동작, 코드 위치, 남은 인수 항목 |
| [작업·운영 규약](docs/workflow.md) | AI 연결·백업·서비스, 개발·검수·증거 관리 |
| [에이전트 지침](AGENTS.md) | 개발 에이전트의 간결한 작업 기준 |
| [구성요소 고지](notices/THIRD_PARTY.md) | 의존성과 모델 이용 고지 |

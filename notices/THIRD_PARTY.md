# 제3자 구성요소와 모델 고지

이 번들은 프로젝트 소스와 빌드된 웹 자산을 포함합니다. Python·Node·Codex 실행 파일, Python 의존성 wheel, 모델 가중치, 인증 파일, 시험용 미디어는 포함하지 않습니다. 설치 시 각 배포처에서 받으며 각 구성요소의 원래 이용조건이 적용됩니다. 이 문서는 프로젝트 자체에 새로운 라이선스를 부여하지 않습니다.

웹 자산에 포함되는 React 19.3.0, React DOM 19.3.0, Scheduler 0.28.0은 설치된 패키지 메타데이터상 MIT입니다. 원문은 이 디렉터리의 `react-LICENSE.txt`, `react-dom-LICENSE.txt`, `scheduler-LICENSE.txt`에 보존합니다. 웹 의존성의 정확한 버전·무결성은 `apps/web/package-lock.json`에 있습니다.

Python 의존성의 버전·배포처·해시는 `uv.lock`, 직접 의존성은 `pyproject.toml`에 고정되어 있습니다. WhisperX, PyTorch/torchaudio/torchvision, CTranslate2, faster-whisper, pyannote.audio 등은 설치 시 배포 패키지의 라이선스·고지를 함께 받습니다. FFmpeg는 Ubuntu 시스템 패키지로 별도 설치합니다.

기존 설치와 M3의 고정 SDK 설치에서 읽은 Python 의존성의 버전·라이선스 메타데이터·라이선스 파일 목록은 `PYTHON_DEPENDENCIES.json`에 있습니다. 메타데이터가 비어 있으면 이용 허가를 뜻하지 않으며 해당 배포처의 조건을 확인해야 합니다.

모델 카드의 고정 리비전에서 확인한 라이선스 필드는 다음과 같습니다. 가중치는 재배포하지 않습니다.

| 모델 | 모델 카드 라이선스 필드 | 고정 리비전 |
|---|---|---|
| mobiuslabsgmbh/faster-whisper-large-v3-turbo | MIT | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf |
| kresnik/wav2vec2-large-xlsr-korean | Apache-2.0 | 629c9a3501c10ba128bf3fa1eebb12af3be03f61 |
| pyannote/speaker-diarization-community-1 | CC-BY-4.0 | 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee |

영어 정렬은 torchaudio의 WAV2VEC2_ASR_BASE_960H 체크포인트를 사용합니다. 다운로드 URL·크기·SHA256과 모든 고정 모델 파일은 `src/meeting_minutes/model_manifest.json`에 있습니다. WhisperX 패키지에 포함된 VAD 자산은 해당 패키지 고지에 따릅니다.

pyannote 모델은 소유자가 Hugging Face 모델 페이지의 사용조건·연락처 공유 요구를 직접 확인하고 동의·로그인해야 할 수 있습니다. 설치 도구는 이를 대신 제출하지 않습니다. 구독 Codex 사용에도 소유자의 계정 조건과 서버 데이터 정책이 별도로 적용됩니다.

샘플 음성은 릴리즈에 포함하지 않습니다. 개발 검증의 한국어 샘플은 fomos esports의 CC BY 3.0 자료, 영어 샘플은 PyCon Austria 주최자 고지의 CC BY 자료를 사용했습니다. 장시간 반복 파일도 배포하지 않습니다.

## YouTube 획득 의존성

| 구성요소 | 고정 버전 | 배포 메타데이터의 라이선스 |
|---|---|---|
| yt-dlp | 2026.8.19 | Unlicense |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC |
| Deno | 2.9.7 | MIT 및 포함된 제3자 구성요소 고지 |
| bubblewrap (Ubuntu 24.04 사용자 영역 대체 패키지) | 0.9.0-1ubuntu0.3 | LGPL-2+ 및 Debian copyright 파일의 개별 고지 |

Python 배포물은 `uv.lock`, Deno 아카이브 SHA-256은 `scripts/setup-youtube.py`로 고정합니다. 도구는 설치 시 원 배포처에서 받고 앱 소스 번들에 실행 파일을 넣지 않습니다. 설치된 Python 배포물의 `dist-info/licenses`, bubblewrap의 `usr/share/doc/bubblewrap/copyright`, [Deno 라이선스](https://github.com/denoland/deno/blob/v2.9.7/LICENSE.md)를 함께 확인하세요. EJS에 포함된 MIT/ISC 구성요소 고지는 해당 배포 소스의 고지를 따릅니다.

공개 YouTube URL은 재사용 허가를 보장하지 않습니다. 소유하거나 재사용·처리가 허용된 영상만 입력하며 로그인·유료·지역 제한을 우회하지 않습니다. 모델 입력에는 획득한 음성의 전사를 사용하고 썸네일·자막으로 대체하지 않습니다.

## Gemini API SDK

공식 `google-genai` 2.25.0과 추가 의존성은 `uv.lock`으로 고정합니다. 설치된 SDK의 라이선스 메타데이터는 Apache-2.0이며 상세 의존성 고지는 `PYTHON_DEPENDENCIES.json`에 포함합니다. SDK 라이선스와 Gemini 서비스의 데이터 처리·과금 조건은 별개입니다. 실제 이용 전에 [Gemini API 서비스 조건](https://ai.google.dev/gemini-api/terms)을 확인해야 합니다.

## 선택적 Claude 런타임

공식 `@anthropic-ai/claude-code-linux-x64` 2.1.283 실행 파일은 소스 번들에 포함하지 않습니다. `scripts/setup-claude.py`가 고정 공개 배포 URL과 SHA-512를 검증해 사용자 도구 폴더에 설치합니다. 사용에는 별도의 Claude 구독과 [공식 사용 조건](https://code.claude.com/docs/en/legal-and-compliance)이 적용됩니다.

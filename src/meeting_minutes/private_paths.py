"""공유·백업·릴리즈에 넣을 수 없는 인증 파일 경로입니다."""
from pathlib import PurePath


PRIVATE_PARTS = {'.apikey', '.claude', '.codex', 'codex-home', 'claude-home',
                 'runtime-user-home', 'claude-user-home'}
PRIVATE_NAMES = {'auth.json', '.credentials.json', 'owner-key', 'gemini.json', '.gemini_api_key', '.claude.json'}


def private_path(path):
    parts = PurePath(str(path).replace('\\', '/')).parts
    return any(part in PRIVATE_PARTS or part in PRIVATE_NAMES or part.startswith(('.gemini-', '.claude.json.')) for part in parts)

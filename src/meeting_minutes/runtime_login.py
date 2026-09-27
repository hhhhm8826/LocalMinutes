"""소유자가 로컬 터미널에서 수행하는 분리된 구독 로그인."""
import argparse
import json
import os
import tempfile

from .codex_provider import CodexCliProvider
from .settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instructions', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    if args.instructions:
        print(json.dumps({'command': 'bash scripts/login-codex.sh', 'runtime_home': str(settings.codex_home),
                          'runtime_cli': str(settings.codex_cli), 'copy_development_auth': False,
                          'instruction': '소유자가 이 사용자 계정의 로컬 터미널에서 직접 로그인하세요. 인증 파일을 복사하지 마세요.'}, ensure_ascii=False, indent=2))
        return
    if not settings.codex_cli.is_file():
        parser.error('Codex CLI를 먼저 설치하세요.')
    env = CodexCliProvider(settings).environment()
    # 개발 저장소의 지침이나 설정을 로그인 작업에 상속하지 않는다.
    with tempfile.TemporaryDirectory(prefix='minutes-login-') as folder:
        import subprocess
        code = subprocess.call([str(settings.codex_cli), 'login'], cwd=folder, env=env)
    raise SystemExit(code)


if __name__ == '__main__':
    os.umask(0o077)
    main()

"""사용자 서비스 파일 생성. 활성화는 소유자가 별도로 수행한다."""
import argparse
import json
import os
from pathlib import Path

from .settings import Settings


def quote(value, *, command=False):
    value = str(value).replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('서비스 경로에 제어 문자를 사용할 수 없습니다.')
    return json.dumps(value, ensure_ascii=False)


def render(root, settings):
    root = root.resolve()
    env = {'HOME': str(Path.home()), 'PATH': '/usr/local/bin:/usr/bin:/bin',
           'MINUTES_DATA_DIR': str(settings.data_dir), 'MINUTES_CONFIG_DIR': str(settings.config_dir),
           'MINUTES_CACHE_DIR': str(settings.cache_dir), 'MINUTES_CODEX_HOME': str(settings.codex_home),
           'MINUTES_CODEX_USER_HOME': str(settings.codex_user_home), 'MINUTES_CODEX_CLI': str(settings.codex_cli),
           'MINUTES_WEB_DIR': str(root / 'apps/web/dist'), 'MINUTES_ORIGIN': settings.origin,
           'MINUTES_THREADS': str(settings.threads), 'MINUTES_CODEX_MODEL': settings.codex_model,
           'MINUTES_CODEX_TIMEOUT_SECONDS': str(settings.codex_timeout_seconds),
           'MINUTES_CODEX_INPUT_BYTES': str(settings.codex_input_bytes), 'MINUTES_CODEX_MAX_CALLS': str(settings.codex_max_calls),
           'MINUTES_CLAUDE_CLI': str(settings.claude_cli), 'MINUTES_CLAUDE_HOME': str(settings.claude_home),
           'MINUTES_CLAUDE_USER_HOME': str(settings.claude_user_home),
           'MINUTES_YOUTUBE_DENO': str(settings.youtube_deno), 'MINUTES_YOUTUBE_BWRAP': str(settings.youtube_bwrap)}
    lines = ['[Unit]', 'Description=Local Meeting Minutes', 'After=network.target', '', '[Service]', 'Type=simple',
             'WorkingDirectory=' + str(root).replace('%', '%%'),
             'ExecStart=/bin/bash ' + quote(root / 'scripts/run.sh', command=True),
             'ExecStop=/bin/bash ' + quote(root / 'scripts/stop.sh', command=True),
             *['Environment=' + quote(key + '=' + value) for key, value in env.items()],
             'UMask=0077', 'Restart=on-failure', 'RestartSec=5', 'TimeoutStopSec=30', 'KillMode=mixed',
             'CPUQuota=' + str(settings.threads * 100) + '%', 'MemoryMax=22G', 'TasksMax=256',
             'NoNewPrivileges=true', 'StandardOutput=journal', 'StandardError=journal', '',
             '[Install]', 'WantedBy=default.target', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-root', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.install_root.expanduser().resolve()
    if not (root / 'scripts/run.sh').is_file() or not (root / '.venv/bin/python').is_file():
        parser.error('설치된 앱 디렉터리가 필요합니다.')
    destination = args.output or Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user/local-meeting-minutes.service'
    if not destination.is_absolute():
        parser.error('서비스 출력 경로는 절대 경로여야 합니다.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open('x', encoding='utf-8') as stream:
        stream.write(render(root, Settings()))
    destination.chmod(0o600)
    print(json.dumps({'service_file': str(destination), 'enabled': False,
                      'next': ['systemctl --user daemon-reload', 'systemctl --user enable --now local-meeting-minutes']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

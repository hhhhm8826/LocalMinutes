"""Local installation checks; optional AI setup never fails the local application."""
import json
import platform
import shutil
import sqlite3

from .diagnostics import model_cache, runtime_status
from .settings import Settings
from .youtube_sandbox import youtube_readiness


def ai_status(settings, runtime):
    # No migration, credential reads, remote checks or Claude login during doctor.
    active, policy_code = 'codex_cli', 'AI_POLICY_NOT_INITIALIZED'
    models = {'codex_cli': settings.codex_model, 'gemini_api':'gemini-3.8-flash', 'claude_cli':'claude-opus-5-5'}
    if settings.database_path.is_file():
        try:
            with sqlite3.connect(settings.database_path.as_uri() + '?mode=ro', uri=True) as connection:
                row = connection.execute('SELECT active_provider,models_json FROM ai_policy WHERE id=1').fetchone()
            if row and row[0] in {'codex_cli', 'gemini_api', 'claude_cli'}:
                stored = json.loads(row[1])
                if not isinstance(stored, dict) or any(not isinstance(stored.get(name), str) for name in models):
                    raise ValueError('invalid policy')
                models = {name: stored[name] for name in models}
                active, policy_code = row[0], None
        except (sqlite3.Error, ValueError, TypeError):
            policy_code = 'AI_POLICY_NOT_INITIALIZED'
    codex_ready = (runtime['login'] == 'chatgpt' and runtime['version'] == '0.157.1'
                   and runtime['configured_model_in_local_catalog'] is True and runtime['error'] is None
                   and models['codex_cli'] == runtime.get('configured_model', settings.codex_model))
    key_path = settings.config_dir / '.apikey' / 'gemini.json'
    key_present = key_path.is_file() and not key_path.is_symlink()
    claude_installed = settings.claude_cli.is_file()
    providers = {
        'codex_cli': {'label': 'Codex CLI', 'local_ready': codex_ready,
                      'code': None if codex_ready else runtime['error'] or 'AI_CONNECTION_CHECK_REQUIRED'},
        'gemini_api': {'label': 'Gemini API', 'local_ready': False, 'credential_file_present': key_present,
                       'code': 'AI_CONNECTION_CHECK_REQUIRED' if key_present else 'AI_AUTH_REQUIRED'},
        'claude_cli': {'label': 'Claude CLI', 'local_ready': False, 'installed': claude_installed,
                       'code': 'AI_CONNECTION_CHECK_REQUIRED' if claude_installed else 'AI_INSTALL_REQUIRED'},
    }
    for name, provider in providers.items():
        provider['configured_model'] = models[name]
    return {'active_provider': active, 'policy_code': policy_code, 'providers': providers,
            'optional_for_local_processing': True, 'remote_generation_tested': False,
            'next': '소유자 설정의 AI 연결 및 선택에서 인증·모델 접근을 확인하세요. 파일 존재는 인증 성공을 뜻하지 않습니다.'}


def report(settings):
    runtime, models = runtime_status(settings), model_cache(settings)
    youtube = youtube_readiness(settings)
    checks = {'linux_x86_64': platform.system() == 'Linux' and platform.machine() == 'x86_64',
              'python_312': platform.python_version_tuple()[:2] == ('3', '12'),
              'ffmpeg': shutil.which('ffmpeg') is not None, 'ffprobe': shutil.which('ffprobe') is not None,
              'database_exists': settings.database_path.is_file(),
              'model_files_present': all(model['files_present'] for model in models['models']),
              'youtube_tools_and_isolation': youtube['ready']}
    ready = all(checks.values())
    return {'status': 'LOCAL_CHECKS_PASS' if ready else 'PREPARATION_REQUIRED', 'checks': checks,
            'data_dir': str(settings.data_dir), 'runtime': runtime, 'models': models, 'youtube': youtube,
            'ai': ai_status(settings, runtime), 'inference_tested': False, 'remote_generation_tested': False}


def main():
    result = report(Settings())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'LOCAL_CHECKS_PASS' else 2)


if __name__ == '__main__':
    main()

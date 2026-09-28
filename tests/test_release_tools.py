import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from meeting_minutes.service import quote, render
from meeting_minutes.settings import Settings


def test_alternate_home_login_instructions_and_doctor_do_not_reuse_auth(tmp_path):
    home = tmp_path / '다른 사용자 홈'
    home.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(('MINUTES_', 'XDG_', 'HF_'))}
    env['HOME'] = str(home)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-m', 'meeting_minutes.runtime_login', '--instructions'],
                            cwd=root, env=env, capture_output=True, text=True, check=True)
    instructions = json.loads(result.stdout)
    assert instructions['runtime_home'].startswith(str(home))
    assert instructions['runtime_cli'].startswith(str(home))
    assert instructions['copy_development_auth'] is False
    result = subprocess.run([sys.executable, '-m', 'meeting_minutes.doctor'], cwd=root, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 2
    diagnosis = json.loads(result.stdout)
    assert diagnosis['status'] == 'PREPARATION_REQUIRED'
    assert diagnosis['runtime']['login'] != 'chatgpt'
    assert not diagnosis['checks']['model_files_present']
    assert not diagnosis['checks']['database_exists']
    assert not list(home.rglob('auth.json'))


def test_service_unit_quotes_paths_and_does_not_enable_it(tmp_path):
    root = tmp_path / '설치 경로 % dollar$'
    settings = Settings(data_dir=tmp_path / 'data', config_dir=tmp_path / 'config', cache_dir=tmp_path / 'cache')
    unit = render(root, settings)
    assert '%%' in unit and 'dollar$$/scripts/run.sh' in unit
    assert 'MINUTES_DATA_DIR=' + str(settings.data_dir) in unit
    assert 'KillMode=mixed' in unit and 'MemoryMax=22G' in unit
    assert 'User=' not in unit and 'sudo' not in unit
    with pytest.raises(ValueError):
        quote('bad\npath')


def test_optional_ai_diagnostics_do_not_read_credentials_or_migrate(tmp_path, monkeypatch):
    from meeting_minutes import doctor
    import sqlite3
    settings = Settings(data_dir=tmp_path/'data', config_dir=tmp_path/'config', cache_dir=tmp_path/'cache',
                        claude_cli=tmp_path/'missing-claude')
    settings.prepare()
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('CREATE TABLE ai_policy (id INTEGER, active_provider TEXT, models_json TEXT)')
        connection.execute("INSERT INTO ai_policy VALUES (1,'gemini_api',?)", (json.dumps({'codex_cli':'configured-custom','gemini_api':'gemini-3.5-flash','claude_cli':'claude-sonnet-4-6'}),))
    keys = settings.config_dir/'.apikey'
    keys.mkdir()
    (keys/'gemini.json').write_text('FAKE-SECRET-MUST-NOT-BE-READ')
    original_read = Path.read_text
    def guarded(path, *args, **kwargs):
        if path == keys/'gemini.json':
            raise AssertionError('doctor must not read credentials')
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded)
    monkeypatch.setattr(doctor,'runtime_status',lambda settings: {'login':'not_logged_in','version':None,
        'configured_model_in_local_catalog':False,'error':'CODEX_INSTALL_REQUIRED'})
    monkeypatch.setattr(doctor,'model_cache',lambda settings: {'models':[{'files_present':True}]})
    monkeypatch.setattr(doctor,'youtube_readiness',lambda settings: {'ready':True})
    monkeypatch.setattr(doctor.shutil,'which',lambda command: '/fake/bin/'+command)
    monkeypatch.setattr(doctor.platform,'system',lambda: 'Linux')
    monkeypatch.setattr(doctor.platform,'machine',lambda: 'x86_64')
    monkeypatch.setattr(doctor.platform,'python_version_tuple',lambda: ('3','12','0'))
    before = settings.database_path.read_bytes()
    result = doctor.report(settings)
    assert result['status'] == 'LOCAL_CHECKS_PASS'
    assert result['ai']['active_provider'] == 'gemini_api'
    assert set(result['ai']['providers']) == {'codex_cli','gemini_api','claude_cli'}
    assert result['ai']['providers']['gemini_api']['credential_file_present']
    assert not any(p['local_ready'] for p in result['ai']['providers'].values())
    assert 'FAKE-SECRET' not in json.dumps(result)
    assert settings.database_path.read_bytes() == before
    assert not (keys/'.lock').exists()


def test_service_keeps_custom_runtime_paths_without_inherited_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','FAKE-SERVICE-SECRET')
    settings = Settings(data_dir=tmp_path/'data',config_dir=tmp_path/'config',cache_dir=tmp_path/'cache',
        claude_cli=tmp_path/'tools/claude',claude_home=tmp_path/'runtime/claude',claude_user_home=tmp_path/'runtime/home',
        codex_timeout_seconds=123,codex_input_bytes=12000,codex_max_calls=7)
    unit=render(tmp_path,settings)
    for field in ('claude_cli','claude_home','claude_user_home','config_dir','codex_timeout_seconds','codex_input_bytes','codex_max_calls'):
        assert 'MINUTES_'+field.upper()+'='+str(getattr(settings,field)) in unit
    assert 'FAKE-SERVICE-SECRET' not in unit and 'GEMINI_API_KEY' not in unit

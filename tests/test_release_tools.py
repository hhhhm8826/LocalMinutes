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

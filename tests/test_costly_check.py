from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/check-costly.py'
    spec = importlib.util.spec_from_file_location('costly_check', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value, path


def test_budget_is_reserved_once_under_concurrency_and_wrong_fixture_rejected(tmp_path):
    check, _ = module()
    path = tmp_path / 'budget.json'
    path.write_text(json.dumps({'max_model_runs': 1, 'max_wall_seconds': 30,
                                'allowed_fixture_sha256': ['fixture']}))
    with pytest.raises(ValueError):
        check.reserve(path, 'model_runs', 'unapproved')
    assert 'reserved_model_runs' not in json.loads(path.read_text())
    def attempt(_):
        try:
            return check.reserve(path, 'model_runs', 'fixture')
        except ValueError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count(30) == 1 and results.count('rejected') == 1
    assert json.loads(path.read_text())['reserved_model_runs'] == 1


def test_costly_modes_require_explicit_fixture_budget_and_output():
    _, path = module()
    for mode in ('model', 'live-codex', 'soak'):
        result = subprocess.run([sys.executable, str(path), mode], capture_output=True, text=True)
        assert result.returncode == 2
        assert '--fixture' in result.stderr and '--budget' in result.stderr


def test_soak_requires_user_request_before_reading_files_or_creating_output(tmp_path):
    _, path = module()
    output = tmp_path / 'result'
    result = subprocess.run([sys.executable, str(path), 'soak',
                             '--fixture', str(tmp_path / 'missing.json'),
                             '--budget', str(tmp_path / 'budget.json'), '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert '--user-requested-soak' in result.stderr
    assert not output.exists()

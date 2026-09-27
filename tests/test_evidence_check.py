import importlib.util
import json
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        'evidence_check', Path(__file__).resolve().parents[1] / 'scripts/check-evidence.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_changed_inputs_and_tampered_evidence_cannot_pass(tmp_path):
    check = module()
    source = tmp_path / 'speech.py'
    source.write_text('old')
    evidence = tmp_path / 'run.json'
    evidence.write_text('{"status":"PASS"}')
    manifest = tmp_path / 'index.json'
    entries = [{'id': name, 'status': 'PASS', 'path': evidence.name,
                'sha256': check.digest(evidence), 'inputs': {source.name: check.digest(source)}}
               for name in check.REQUIRED]
    manifest.write_text(json.dumps({'schema_version': 1, 'checks': entries}))
    assert check.audit(tmp_path, manifest)['status'] == 'PASS'
    source.write_text('changed')
    assert all('STALE_INPUTS' in row['reasons'] for row in check.audit(tmp_path, manifest)['findings'])
    evidence.write_text('{"status":"FAIL"}')
    assert all('EVIDENCE_CHANGED_OR_MISSING' in row['reasons']
               for row in check.audit(tmp_path, manifest)['findings'])
    with pytest.raises(ValueError):
        check.inside(tmp_path, '../outside.json')


def test_soak_is_optional_and_audited_only_when_requested(tmp_path):
    check = module()
    source = tmp_path / 'source.py'
    source.write_text('fixture')
    evidence = tmp_path / 'run.json'
    evidence.write_text('{}')
    entries = [{'id': name, 'status': 'PASS', 'path': evidence.name,
                'sha256': check.digest(evidence), 'inputs': {source.name: check.digest(source)}}
               for name in check.REQUIRED]
    manifest = tmp_path / 'index.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'checks': entries}))
    assert check.audit(tmp_path, manifest)['status'] == 'PASS'
    assert check.audit(tmp_path, manifest, include_soak=True)['findings'] == [
        {'id': 'soak', 'reason': 'MISSING'}]
    entries.append({'id': 'soak', 'status': 'FAIL', 'path': 'missing.json',
                    'sha256': 'invalid', 'inputs': {source.name: 'stale'}})
    manifest.write_text(json.dumps({'schema_version': 1, 'checks': entries}))
    assert check.audit(tmp_path, manifest)['status'] == 'PASS'
    findings = check.audit(tmp_path, manifest, include_soak=True)['findings']
    assert len(findings) == 1 and findings[0]['id'] == 'soak'
    assert 'STALE_INPUTS' in findings[0]['reasons']

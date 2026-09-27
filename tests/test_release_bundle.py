import importlib.util
from pathlib import Path
import subprocess

import pytest


def builder():
    spec = importlib.util.spec_from_file_location(
        'release_builder', Path(__file__).resolve().parents[1] / 'scripts/build-release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundle_excludes_unrelated_json_and_rejects_missing_docs_and_symlinks(tmp_path):
    module = builder()
    required = set(module.REQUIRED) | {'scripts/' + name for name in module.SCRIPTS}
    required.update('apps/web/' + name for name in
                    ('index.html', 'playwright.config.ts', 'tsconfig.json', 'vite.config.ts'))
    for name in required:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture')
    for name in ('apps/web/auth.json', '.workflow/private.json', 'data/meeting.json'):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('private')
    assert set(module.collect(tmp_path)) == required
    doc = tmp_path / 'docs/workflow.md'
    doc.unlink()
    with pytest.raises(ValueError, match='Invalid release file'):
        module.collect(tmp_path)
    doc.symlink_to(tmp_path / 'README.md')
    with pytest.raises(ValueError, match='Invalid release file'):
        module.collect(tmp_path)


def test_bundle_uses_git_bytes_despite_checkout_newline_conversion(tmp_path):
    module = builder()
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    source = tmp_path / 'source.py'
    source.write_bytes(b'line1\nline2\n')
    subprocess.run(['git', 'add', 'source.py'], cwd=tmp_path, check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                    'commit', '-qm', 'fixture'], cwd=tmp_path, check=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    source.write_bytes(b'line1\r\nline2\r\n')
    asset = tmp_path / 'apps/web/dist/index.html'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'built asset')
    files = module.revision_payloads(tmp_path, ['source.py', 'apps/web/dist/index.html'], commit)
    assert files['source.py'] == b'line1\nline2\n'
    assert files['apps/web/dist/index.html'] == b'built asset'

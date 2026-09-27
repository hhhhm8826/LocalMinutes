import hashlib
from pathlib import Path

import pytest

from meeting_minutes.model_prepare import prepare
from meeting_minutes.settings import Settings


def test_pinned_cache_manifest_requires_consent_and_checks_corruption(tmp_path, monkeypatch):
    settings = Settings(cache_dir=tmp_path / 'cache')
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    payload = b'model-fixture'
    entry = {'path': 'model.bin', 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    (snapshot / 'model.bin').write_bytes(payload)
    english = settings.cache_dir / 'torch/hub/checkpoints/align.pth'
    english.parent.mkdir(parents=True)
    english.write_bytes(payload)
    manifest = {'schema_version': 1, 'models': [{'repo_id': 'fixture/model', 'revision': 'fixed-revision', 'files': [entry]}],
                'english_alignment': entry | {'filename': 'align.pth', 'url': 'https://example.invalid/not-used'}}
    calls = []
    def download(repo, **options):
        calls.append((repo, options))
        assert options['revision'] == 'fixed-revision' and options['local_files_only']
        return str(snapshot)
    monkeypatch.setattr('huggingface_hub.snapshot_download', download)
    with pytest.raises(ValueError):
        prepare(settings, manifest, check_only=False, accept_terms=False)
    assert calls == []
    assert prepare(settings, manifest, check_only=True, accept_terms=False)['status'] == 'CACHE_HASHES_VERIFIED'
    Path(snapshot / 'model.bin').write_bytes(b'x' * len(payload))
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        prepare(settings, manifest, check_only=True, accept_terms=False)

import fcntl
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

import pytest
from sqlalchemy import text

from meeting_minutes.operations import backup, restore
from meeting_minutes.settings import Settings
from meeting_minutes.storage import make_engine
from meeting_minutes.retention import policy, reap_retention
from meeting_minutes.repository import Repository
from test_minutes_management import ready
from test_queue_media import context  # noqa: F401


def test_backup_restore_new_path_without_auth_and_lock_protection(context, tmp_path):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with repo.write() as connection:
        connection.execute(text("INSERT INTO owner_sessions VALUES ('secret-hash','secret-csrf',9999999999)"))
        connection.execute(text("INSERT INTO local_sessions VALUES ('local-secret-hash','local-secret-csrf',9999999999)"))
    reap_retention(repo, settings, meeting['input_received_at'] + 31 * 86400)
    expired = repo.meeting(meeting['id'])
    destination = tmp_path / 'backup.tar.gz'
    with (settings.data_dir / 'app.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='정지'):
            backup(settings, destination)
    backup(settings, destination)
    with tarfile.open(destination) as archive:
        names = archive.getnames()
        assert 'minutes.sqlite3' in names and not any('auth' in name or 'logs' in name for name in names)
        assert b'secret-csrf' not in archive.extractfile('minutes.sqlite3').read()
        manifest = json.load(archive.extractfile('manifest.json'))
        assert set(manifest['files']) == set(names) - {'manifest.json'}
    restored = tmp_path / '새 경로' / '복원 자료'
    restore(destination, restored)
    engine = make_engine(restored / 'minutes.sqlite3')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT title FROM meetings WHERE id=:id'), {'id': meeting['id']}).scalar_one() == meeting['title']
        assert connection.execute(text('SELECT COUNT(*) FROM owner_sessions')).scalar_one() == 0
        assert connection.execute(text('SELECT COUNT(*) FROM local_sessions')).scalar_one() == 0
    restored_repo = Repository(engine)
    assert restored_repo.meeting(meeting['id']) == expired
    assert policy(restored_repo) == policy(repo)
    engine.dispose()
    with pytest.raises(RuntimeError):
        restore(destination, restored)
    original = destination.read_bytes()
    with pytest.raises(RuntimeError):
        backup(settings, destination)
    assert destination.read_bytes() == original


def test_restore_rejects_traversal_before_creating_destination(tmp_path):
    source = tmp_path / 'bad.tar.gz'
    with tarfile.open(source, 'w:gz') as archive:
        member = tarfile.TarInfo('../outside')
        member.size = 1
        archive.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(RuntimeError, match='허용되지'):
        restore(source, tmp_path / 'restore')
    assert not (tmp_path / 'restore').exists() and not (tmp_path / 'outside').exists()


def test_restore_rejects_payload_corruption_before_publishing(context, tmp_path):  # noqa: F811
    settings, _ = context
    (settings.data_dir / 'media/fixture.wav').write_bytes(b'original payload')
    source = tmp_path / 'source.tar.gz'
    backup(settings, source)
    corrupt = tmp_path / 'corrupt.tar.gz'
    with tarfile.open(source) as incoming, tarfile.open(corrupt, 'w:gz') as outgoing:
        for member in incoming.getmembers():
            payload = incoming.extractfile(member).read()
            if member.name == 'media/fixture.wav':
                payload = b'x' * len(payload)
            outgoing.addfile(member, io.BytesIO(payload))
    with pytest.raises(RuntimeError, match='해시'):
        restore(corrupt, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_server_stop_uses_process_identity_and_releases_lock(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', config_dir=tmp_path / 'config', cache_dir=tmp_path / 'cache',
                        worker_enabled=False, origin='http://127.0.0.1:8878')
    env = os.environ | {'MINUTES_DATA_DIR': str(settings.data_dir), 'MINUTES_CONFIG_DIR': str(settings.config_dir),
                        'MINUTES_CACHE_DIR': str(settings.cache_dir), 'MINUTES_WORKER_ENABLED': 'false', 'MINUTES_ORIGIN': settings.origin}
    process = subprocess.Popen([sys.executable, '-m', 'meeting_minutes.server', '--data-dir', str(settings.data_dir)],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(settings.origin + '/api/health', timeout=.5) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('server not ready')
        record = settings.data_dir / 'app-process.json'
        value = json.loads(record.read_text())
        value['created_at'] += 6
        record.write_text(json.dumps(value))
        # The CLI is a sibling, so it cannot reap this parent's server child.
        result = subprocess.run([sys.executable, '-m', 'meeting_minutes.operations', 'stop'],
                                env=env, capture_output=True, text=True, timeout=8)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['status'] == 'stopped'
        process.wait(timeout=3)
        assert not (settings.data_dir / 'app-process.json').exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

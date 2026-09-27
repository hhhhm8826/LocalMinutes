import sys
import threading
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.deletion import meeting_file_lock, purge_meeting, reap_deletions, request_delete
from meeting_minutes.repository import Conflict, Missing
from meeting_minutes.worker import Worker
from test_minutes_management import complete, ready
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.minutes_management import queue_generation
from test_queue_media import context, register  # noqa: F401


def test_delete_removes_owned_files_and_all_versions_requires_csrf(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'delete-minutes-fixture')
    complete(repo, queued, version)
    job = repo.jobs()[0]
    artifact = settings.data_dir / 'artifacts' / f'{job["id"]}-{job["attempt_id"]}-diarize.json'
    artifact.write_text('{"embeddings":[1]}')
    unrelated = settings.data_dir / 'artifacts' / 'unrelated.json'
    unrelated.write_text('keep')
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        path = f'/api/meetings/{meeting["id"]}'
        assert client.delete(path, headers={'origin': settings.origin}).status_code == 401
        csrf = client.post('/api/auth/login', headers={'origin': settings.origin},
                          json={'key': settings.owner_key_path.read_text()}).json()['csrf_token']
        assert client.delete(path, headers={'origin': settings.origin}).status_code == 403
        response = client.delete(path, headers={'origin': settings.origin, 'x-csrf-token': csrf})
        assert response.status_code == 200 and response.json()['status'] == 'deleted'
        assert client.get(path).status_code == 404
        assert client.get(f'/api/deletions/{meeting["id"]}').json()['status'] == 'deleted'
        assert client.delete(path, headers={'origin': settings.origin, 'x-csrf-token': csrf}).status_code == 200
    assert not artifact.exists() and unrelated.read_text() == 'keep'
    assert not list((settings.data_dir / 'media').iterdir())
    with repo.engine.connect() as connection:
        for table in ('meetings', 'media_assets', 'jobs', 'transcript_versions', 'minutes_revisions', 'usage_records', 'stage_artifacts', 'job_requests'):
            assert connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one() == 0


def test_running_delete_waits_for_owned_process_and_rejects_late_save(context):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    worker = Worker(settings, repo.engine, [sys.executable, '-c', 'import sys,time; sys.stdin.readline(); time.sleep(60)'])
    errors = []
    def execute():
        try:
            worker.execute(job)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=execute)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not repo.job(job['id'])['pid'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert repo.job(job['id'])['pid']
        request_delete(repo, job['meeting_id'])
        assert not purge_meeting(repo, settings, job['meeting_id'])
        with pytest.raises(Conflict, match='STALE_ATTEMPT'):
            repo.stage(job, 'SAVE')
        thread.join(8)
        assert not thread.is_alive() and not errors
        assert repo.job(job['id'])['state'] == 'CANCELLED'
        assert purge_meeting(repo, settings, job['meeting_id'])
        with pytest.raises(Missing):
            repo.meeting(job['meeting_id'])
    finally:
        worker.stopping = True
        thread.join(8)


def test_upload_lock_and_interrupted_cleanup_preserve_pending_request(context, monkeypatch):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with meeting_file_lock(settings, meeting['id']):
        request_delete(repo, meeting['id'])
        assert not purge_meeting(repo, settings, meeting['id'])
    from pathlib import Path
    original = Path.unlink
    def fail(*args, **kwargs):
        raise PermissionError('temporary failure')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail)
        reap_deletions(repo, settings)
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT deleted_at FROM meetings WHERE id=:id'), {'id': meeting['id']}).scalar_one()
    assert Path.unlink is original
    reap_deletions(repo, settings)
    assert repo.meetings() == [] and repo.jobs() == []

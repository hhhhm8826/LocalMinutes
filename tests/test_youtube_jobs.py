from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_minutes.api import create_app
from meeting_minutes.files import file_hash
from meeting_minutes.repository import Conflict
from meeting_minutes.youtube_jobs import register_youtube
from meeting_minutes.youtube_runner import acquire_job, supervise
from meeting_minutes.youtube_sandbox import sandbox_command
from meeting_minutes.processes import ProcessFailure
from test_queue_media import context, wav_bytes  # noqa: F401
from test_youtube_policy import URL


def test_url_registration_is_async_idempotent_and_global_queue(context, monkeypatch):  # noqa: F811
    settings, repo = context
    def forbidden(*args, **kwargs):
        raise AssertionError('web registration must not acquire media')
    monkeypatch.setattr('meeting_minutes.youtube_runner.supervise', forbidden)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        headers = {'origin': settings.origin}
        headers['x-csrf-token'] = client.post('/api/auth/local', headers=headers).json()['csrf_token']
        headers['idempotency-key'] = 'youtube-request-001'
        first = client.post('/api/videos/youtube', json={'url': URL + '&t=9&list=x'}, headers=headers)
        assert first.status_code == 202
        repeated = client.post('/api/videos/youtube', json={'url': URL}, headers=headers)
        assert first.json()['id'] == repeated.json()['id']
        assert first.json()['media_id'] is None and first.json()['stage'] == 'SOURCE_CHECK'
        assert len(repo.meetings()) == 1
        headers['idempotency-key'] = 'youtube-request-002'
        assert client.post('/api/videos/youtube', json={'url': 'https://127.0.0.1/private'}, headers=headers).status_code == 422
        assert len(repo.meetings()) == 1


@pytest.mark.parametrize('cancel', [False, True])
def test_fenced_publication_after_hash_and_ffprobe(context, monkeypatch, cancel):  # noqa: F811
    settings, repo = context
    register_youtube(repo, URL, '영상', 'youtube-publish-001')
    job = repo.claim()
    monkeypatch.setattr('meeting_minutes.youtube_runner.sandbox_command', lambda *args: ['fixture'])
    def download(command, scratch, stage):
        stage('SOURCE_CHECK')
        stage('DOWNLOAD')
        path = scratch / 'download/audio.source'
        path.write_bytes(wav_bytes(1))
        if cancel:
            repo.cancel(job['id'])
        return {'path': str(path), 'size_bytes': path.stat().st_size, 'sha256': file_hash(path),
                'metadata': {'source_url': URL, 'title': '영상', 'duration_seconds': 1}}
    monkeypatch.setattr('meeting_minutes.youtube_runner.supervise', download)
    if cancel:
        with pytest.raises(Conflict):
            acquire_job(repo, settings, job)
        assert not list((settings.data_dir / 'media').iterdir())
    else:
        updated = acquire_job(repo, settings, job)
        media = repo.media(updated['media_id'])
        assert media['duration_ms'] == 1000
        assert Path(settings.data_dir / 'media' / media['stored_name']).is_file()
        assert repo.meeting(job['meeting_id'])['input_received_at'] is not None
        assert len(repo.jobs()) == 1


def test_supervisor_timeout_kills_child(tmp_path):
    import sys
    with pytest.raises(ProcessFailure, match='TIMEOUT'):
        supervise([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, lambda _: None, seconds=.1)


def test_startup_failure_is_distinct_and_does_not_expose_stderr(tmp_path):
    import sys
    with pytest.raises(ProcessFailure, match='^YOUTUBE_PROCESS_START_FAILED$'):
        supervise([sys.executable, '-c', 'import sys; sys.stderr.write("private fixture"); sys.exit(2)'],
                  tmp_path, lambda _: None)


@pytest.mark.parametrize('expected,code', [(30, 'YOUTUBE_DOWNLOAD_INCOMPLETE'),
                                          (None, 'YOUTUBE_DURATION_UNKNOWN')])
def test_valid_audio_prefix_is_not_published_as_complete(context, monkeypatch, expected, code):  # noqa: F811
    settings, repo = context
    register_youtube(repo, URL, '영상', 'youtube-truncated-001')
    job = repo.claim()
    monkeypatch.setattr('meeting_minutes.youtube_runner.sandbox_command', lambda *args: ['fixture'])
    def download(command, scratch, stage):
        path = scratch / 'download/audio.source'
        path.write_bytes(wav_bytes(1))
        return {'path': str(path), 'size_bytes': path.stat().st_size, 'sha256': file_hash(path),
                'metadata': {'source_url': URL, 'duration_seconds': expected}}
    monkeypatch.setattr('meeting_minutes.youtube_runner.supervise', download)
    with pytest.raises(ProcessFailure, match=code):
        acquire_job(repo, settings, job)
    assert repo.job(job['id'])['media_id'] is None
    assert repo.meeting(job['meeting_id'])['input_received_at'] is None
    assert not list((settings.data_dir / 'media').iterdir())


def test_actual_isolated_child_rejects_url_without_network(context, tmp_path):  # noqa: F811
    import sys
    settings, _ = context
    if not settings.youtube_bwrap.is_file() or not settings.youtube_deno.is_file():
        pytest.skip('YouTube tool preparation required')
    output = tmp_path / 'download'
    output.mkdir()
    command = sandbox_command(settings, tmp_path, [sys.executable, '-m', 'meeting_minutes.youtube_child',
                                                  'https://127.0.0.1/private', output, settings.youtube_deno])
    with pytest.raises(ProcessFailure, match='YOUTUBE_URL_INVALID'):
        supervise(command, tmp_path, lambda _: None)


def test_namespace_timeout_stops_grandchild_writes(context, tmp_path):  # noqa: F811
    import sys
    import time
    settings, _ = context
    if not settings.youtube_bwrap.is_file() or not settings.youtube_deno.is_file():
        pytest.skip('YouTube tool preparation required')
    heartbeat = tmp_path / 'heartbeat'
    child = 'import time,pathlib; p=pathlib.Path(' + repr(str(heartbeat)) + ');\nwhile True:\n p.write_text(str(time.time())); time.sleep(.01)'
    script = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(30)'
    command = sandbox_command(settings, tmp_path, [sys.executable, '-c', script])
    with pytest.raises(ProcessFailure, match='TIMEOUT'):
        supervise(command, tmp_path, lambda _: None, seconds=.5)
    assert heartbeat.exists()
    before = heartbeat.read_text()
    time.sleep(.15)
    assert heartbeat.read_text() == before


def test_recovery_removes_unpublished_download_but_keeps_registered_source(context):  # noqa: F811
    from meeting_minutes.worker import Worker
    from sqlalchemy import text
    settings, repo = context
    register_youtube(repo, URL, '영상', 'youtube-orphan-001')
    job = repo.claim()
    name = f'{job["meeting_id"]}-{job["attempt_id"]}.source'
    output = settings.data_dir / 'media' / name
    partial = settings.data_dir / 'media' / (name + '.part')
    output.write_bytes(b'crash-after-rename')
    partial.write_bytes(b'partial')
    worker = Worker(settings, repo.engine)
    worker.recover()
    assert not output.exists() and not partial.exists()
    assert repo.job(job['id'])['state'] == 'INTERRUPTED'
    output.write_bytes(b'committed')
    with repo.write() as connection:
        connection.execute(text("INSERT INTO media_assets(id,meeting_id,original_name,stored_name,size_bytes,sha256,created_at) VALUES ('fixture',:meeting,'source',:name,9,'hash',0)"),
                           {'meeting': job['meeting_id'], 'name': name})
    worker.cleanup_attempt(job)
    assert output.read_bytes() == b'committed'

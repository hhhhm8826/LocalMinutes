from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import wave

from fastapi.testclient import TestClient
from fastapi import HTTPException
from starlette.requests import ClientDisconnect, Request
from types import SimpleNamespace
import pytest
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.contracts import MeetingCreate
from meeting_minutes.files import safe_file
from meeting_minutes.media import extract, probe
from meeting_minutes.processes import ProcessFailure, bounded_run, group_members, stop_group
from meeting_minutes.repository import Conflict, Repository
from meeting_minutes.settings import Settings
from meeting_minutes.storage import make_engine, migrate
from meeting_minutes.worker import Worker, worker_env
from meeting_minutes.uploads import receive_upload

pytestmark = pytest.mark.integration


def extraction_worker(settings, engine):
    code = ('import meeting_minutes.job_runner as runner; '
            'runner.run_speech=lambda *args: ("BLOCKED", "TEST_EXTRACTION_BOUNDARY"); runner.main()')
    return Worker(settings, engine, [sys.executable, '-c', code])


@pytest.fixture
def context(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', config_dir=tmp_path / 'config',
                        cache_dir=tmp_path / 'cache', worker_enabled=False, codex_home=tmp_path / 'codex')
    settings.prepare()
    settings.codex_home.mkdir()
    # Contract fixture only; no authentication and no real Codex/model invocation.
    (settings.codex_home / 'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'gpt-6-astra', 'context_window': 272000, 'effective_context_window_percent': 95}]}))
    engine = make_engine(settings.database_path)
    migrate(engine)
    yield settings, Repository(engine)
    engine.dispose()


def wav_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x01\x00' * 16000 * seconds)
    return output.getvalue()


def register(context, name='sample.wav'):
    settings, repo = context
    meeting = repo.create_meeting(MeetingCreate(title='시험 회의'))
    data = wav_bytes()
    stored = meeting['id'] + '.wav'
    (settings.data_dir / 'media' / stored).write_bytes(data)
    job, created = repo.register_media(meeting['id'], name, stored, len(data), hashlib.sha256(data).hexdigest(), meeting['id'], meeting['id'])
    assert created
    return job


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('condition timed out')


def test_fifo_atomic_claim_and_blocked_head(context):
    _, repo = context
    first, second = register(context), register(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: repo.claim(), range(2)))
    assert [row['id'] for row in claims if row] == [first['id']]
    repo.finish(first['id'], first['attempt_id'], 'BLOCKED', 'LOGIN_REQUIRED')
    assert repo.claim() is None
    retry = repo.retry(first['id'])
    assert retry['sequence'] == first['sequence'] and retry['attempt_id'] != first['attempt_id']
    assert repo.claim()['id'] == first['id']
    assert not repo.finish(first['id'], first['attempt_id'], 'COMPLETED')
    repo.cancel(first['id'])
    assert not repo.finish(first['id'], retry['attempt_id'], 'COMPLETED')
    assert repo.finish(first['id'], retry['attempt_id'], 'CANCELLED')
    assert repo.claim()['id'] == second['id']


def test_upload_auth_limits_idempotency_and_cleanup(context):
    settings, repo = context
    settings.max_upload_bytes = 40000
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        meeting = repo.create_meeting(MeetingCreate(title='미디어'))
        path = f'/api/meetings/{meeting["id"]}/media'
        assert client.put(path, params={'filename': 'a.wav'}, content=b'abc').status_code == 403
        headers = {'origin': settings.origin}
        result = client.post('/api/auth/login', headers=headers, json={'key': settings.owner_key_path.read_text()})
        headers |= {'x-csrf-token': result.json()['csrf_token'], 'idempotency-key': 'upload-0001'}
        assert client.put(path, params={'filename': '../a.wav'}, headers=headers, content=wav_bytes()).status_code == 422
        assert client.put(path, params={'filename': 'a.exe'}, headers=headers, content=b'abc').status_code == 422
        assert client.put(path, params={'filename': 'a.wav'}, headers=headers, content=b'x'*40001).status_code == 413
        assert not list((settings.data_dir / 'media').iterdir())
        result = client.put(path, params={'filename': '회의.wav'}, headers=headers, content=wav_bytes())
        assert result.status_code == 200, result.text
        again = client.put(path, params={'filename': '회의.wav'}, headers=headers, content=wav_bytes())
        assert again.json()['id'] == result.json()['id']
        assert len(repo.jobs()) == 1 and len(list((settings.data_dir / 'media').iterdir())) == 1
        changed = client.put(path, params={'filename': '회의.wav'}, headers=headers, content=b'changed')
        assert changed.status_code == 409
        assert len(list((settings.data_dir / 'media').iterdir())) == 1


def test_streamed_overflow_disconnect_and_disk_full_cleanup(context, monkeypatch):
    settings, repo = context
    settings.max_upload_bytes = 10
    meeting = repo.create_meeting(MeetingCreate(title='중단된 업로드'))
    def request(events):
        async def receive():
            return events.pop(0)
        return Request({'type': 'http', 'method': 'PUT', 'path': '/', 'headers': [],
                        'app': SimpleNamespace(state=SimpleNamespace(settings=settings, repository=repo))}, receive)
    with pytest.raises(HTTPException) as error:
        asyncio.run(receive_upload(request([{'type': 'http.request', 'body': b'123456', 'more_body': True},
                                            {'type': 'http.request', 'body': b'789012', 'more_body': False}]),
                                   meeting['id'], 'a.wav', 'overflow-key'))
    assert error.value.status_code == 413
    with pytest.raises(ClientDisconnect):
        asyncio.run(receive_upload(request([{'type': 'http.request', 'body': b'123', 'more_body': True},
                                            {'type': 'http.disconnect'}]), meeting['id'], 'a.wav', 'disconnect-key'))
    original = os.open
    def no_space(path, *args, **kwargs):
        if str(path).endswith('.part'):
            raise OSError(28, 'simulated ENOSPC')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', no_space)
    with pytest.raises(HTTPException) as error:
        asyncio.run(receive_upload(request([]), meeting['id'], 'a.wav', 'disk-full-key'))
    assert error.value.status_code == 507
    assert not repo.jobs() and not list((settings.data_dir / 'media').iterdir())


def test_metadata_request_body_is_bounded(context):
    settings, _ = context
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        result = client.post('/api/auth/login', headers={'origin': settings.origin}, content=b'x' * 65537)
        assert result.status_code == 413


def test_actual_ffmpeg_job_and_artifact_reuse(context):
    settings, repo = context
    job = register(context)
    worker = extraction_worker(settings, repo.engine)
    assert worker.acquire()
    second = Worker(settings, repo.engine)
    assert not second.acquire()
    try:
        worker.execute(repo.claim())
        result = repo.job(job['id'])
        assert result['state'] == 'BLOCKED' and result['blocked_reason'] == 'TEST_EXTRACTION_BOUNDARY'
        with repo.engine.connect() as connection:
            artifact = dict(connection.execute(text('SELECT * FROM stage_artifacts')).mappings().one())
        path = settings.data_dir / 'artifacts' / artifact['path']
        with wave.open(str(path), 'rb') as audio:
            assert audio.getframerate() == 16000 and audio.getnchannels() == 1
        stamp = path.stat().st_mtime_ns
        repo.retry(job['id'])
        worker.execute(repo.claim())
        assert path.stat().st_mtime_ns == stamp
        assert json.loads(repo.job(job['id'])['progress_json']) == {'reused': 'EXTRACT'}
    finally:
        worker.close()


def test_cancel_between_child_exit_and_commit(context):
    settings, repo = context
    job = register(context)
    worker = extraction_worker(settings, repo.engine)
    original = worker.repository.finish
    def finish(job_id, attempt, state, reason=None):
        if state == 'BLOCKED':
            assert reason == 'TEST_EXTRACTION_BOUNDARY'
            repo.cancel(job_id)
        return original(job_id, attempt, state, reason)
    worker.repository.finish = finish
    worker.execute(repo.claim())
    assert repo.job(job['id'])['state'] == 'CANCELLED'


def test_media_boundaries_and_protocol_rejection(context, tmp_path):
    settings, _ = context
    source = tmp_path / 'sample.wav'
    source.write_bytes(wav_bytes(2))
    with pytest.raises(ProcessFailure, match='MEDIA_TOO_LONG'):
        probe(source, duration_limit=1)
    with pytest.raises(ProcessFailure, match='MEDIA_TOO_LONG'):
        extract(source, tmp_path / 'short.wav', 0, duration_limit=1)
    playlist = tmp_path / 'remote.wav'
    playlist.write_text('#EXTM3U\nhttp://127.0.0.1:9/private\n')
    with pytest.raises(ProcessFailure):
        probe(playlist)
    target = settings.data_dir / 'media' / 'linked.wav'
    target.symlink_to(source)
    with pytest.raises(ValueError):
        safe_file(target.parent, target.name)
    with pytest.raises(ProcessFailure, match='PROCESS_OUTPUT_LIMIT'):
        bounded_run([sys.executable, '-c', 'print("x" * 50000)'], timeout=5, output_limit=1000)
    with pytest.raises(ProcessFailure, match='PROCESS_TIMEOUT'):
        bounded_run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.1)


def test_multitrack_choice_and_no_audio(context, tmp_path):
    settings, repo = context
    job = register(context)
    media = repo.media(job['media_id'])
    source = settings.data_dir / 'media' / media['stored_name']
    generated = tmp_path / 'two.mkv'
    bounded_run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=0.2',
                 '-f', 'lavfi', '-i', 'sine=frequency=400:duration=0.2', '-map', '0:a', '-map', '1:a',
                 '-c:a', 'pcm_s16le', '-disposition:a:0', '0', '-disposition:a:1', '0', str(generated)], timeout=10)
    data = generated.read_bytes()
    source.write_bytes(data)
    with repo.write() as connection:
        connection.execute(text('UPDATE media_assets SET sha256=:sha,size_bytes=:size WHERE id=:id'),
                           {'sha': hashlib.sha256(data).hexdigest(), 'size': len(data), 'id': media['id']})
    worker = extraction_worker(settings, repo.engine)
    worker.execute(repo.claim())
    assert repo.job(job['id'])['blocked_reason'] == 'AUDIO_TRACK_REQUIRED'
    assert repo.claim() is None
    with pytest.raises(Conflict):
        repo.select_track(job['id'], 999)
    repo.select_track(job['id'], 1)
    worker.execute(repo.claim())
    assert repo.job(job['id'])['blocked_reason'] == 'TEST_EXTRACTION_BOUNDARY'
    video = tmp_path / 'silent.mp4'
    bounded_run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:duration=0.2',
                 '-an', '-c:v', 'mpeg4', str(video)], timeout=10)
    with pytest.raises(ProcessFailure, match='NO_AUDIO_TRACK'):
        probe(video)


def test_concurrent_track_choice_commits_only_winning_selection(context):
    _, repo = context
    job = register(context)
    repo.claim()
    repo.set_media_metadata(job, 1000, [{'index': 0}, {'index': 1}])
    repo.finish(job['id'], job['attempt_id'], 'BLOCKED', 'AUDIO_TRACK_REQUIRED')
    barrier = threading.Barrier(2)
    def select(track):
        barrier.wait()
        try:
            return track, repo.select_track(job['id'], track)
        except Conflict:
            return track, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, (0, 1)))
    winners = [(track, result) for track, result in results if result]
    assert len(winners) == 1
    assert repo.media(job['media_id'])['selected_track'] == winners[0][0]
    assert repo.job(job['id'])['attempt_number'] == 2
    assert repo.job(job['id'])['state'] == 'QUEUED'


@pytest.fixture
def slow_child(tmp_path):
    script = tmp_path / 'slow_child.py'
    script.write_text('''import sys,subprocess,time,signal
if sys.stdin.readline().strip() != "START": sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable,"-c","import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(90)"])
time.sleep(90)
''')
    return script


def test_running_cancel_kills_descendants_and_fences_late_completion(context, slow_child):
    settings, repo = context
    job = register(context)
    claimed = repo.claim()
    worker = Worker(settings, repo.engine, [sys.executable, str(slow_child)])
    thread = threading.Thread(target=worker.execute, args=(claimed,))
    thread.start()
    pid = wait_for(lambda: repo.job(job['id'])['pid'])
    wait_for(lambda: len(group_members(pid)) >= 2)
    start = time.monotonic()
    assert repo.cancel(job['id'])['state'] == 'CANCEL_REQUESTED'
    thread.join(9)
    assert not thread.is_alive()
    assert time.monotonic() - start < 9
    assert not group_members(pid)
    assert repo.job(job['id'])['state'] == 'CANCELLED'
    assert not repo.finish(job['id'], job['attempt_id'], 'COMPLETED')


def test_killed_manager_recovery_requires_explicit_retry(context, slow_child):
    settings, repo = context
    job = register(context)
    code = ('from meeting_minutes.settings import Settings; from meeting_minutes.storage import make_engine; '
            'from meeting_minutes.worker import Worker; import sys; s=Settings(); '
            'Worker(s,make_engine(s.database_path),[sys.executable,sys.argv[1]]).run()')
    manager = subprocess.Popen([sys.executable, '-c', code, str(slow_child)], env=worker_env(settings))
    pid = None
    try:
        pid = wait_for(lambda: repo.job(job['id'])['pid'])
        wait_for(lambda: len(group_members(pid)) >= 2)
        manager.kill()
        manager.wait()
        # WSL 벽시계 보정 후에도 부팅 ID/시작 tick으로 같은 자식을 확인한다.
        with repo.write() as connection:
            connection.execute(text('UPDATE jobs SET process_created_at=process_created_at+6 WHERE id=:id'), {'id': job['id']})
        worker = Worker(settings, repo.engine)
        assert worker.acquire()
        try:
            worker.recover()
            assert not group_members(pid)
            assert repo.job(job['id'])['state'] == 'INTERRUPTED'
            assert repo.claim() is None
            retry = repo.retry(job['id'])
            assert retry['attempt_id'] != job['attempt_id']
            assert repo.claim()['id'] == job['id']
        finally:
            worker.close()
    finally:
        if manager.poll() is None:
            manager.kill()
            manager.wait()
        if pid and group_members(pid):
            stop_group(pid, grace=.1)


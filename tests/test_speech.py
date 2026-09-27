import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.speech import alignment_language, preserve_alignment
from meeting_minutes.speech_pipeline import checkpoint, run_speech, save_original
from meeting_minutes.repository import Conflict
from test_queue_media import context, register  # noqa: F401


def test_mixed_alignment_and_missing_text_fall_back_without_loss():
    assert alignment_language('회의 시작합니다.') == 'ko'
    assert alignment_language('Review API 2.0.') == 'en'
    assert alignment_language('회의 API 확인') is None
    assert alignment_language('1234') is None
    source = {'start': 10., 'end': 13., 'text': 'Review API 123'}
    missing = [{'words': [{'word': 'Review', 'start': 10., 'end': 11.}]}]
    assert preserve_alignment(source, missing)['text'] == source['text']
    assert preserve_alignment(source, missing)['words'] == []
    words = [{'word': 'Review', 'start': 10., 'end': 11.}, {'word': 'API'}, {'word': '123'}]
    assert preserve_alignment(source, [{'words': words}])['words'] == words
    words[0]['end'] = 15.
    assert preserve_alignment(source, [{'words': words}])['words'] == []


def test_checkpoint_reuses_verified_data_rejects_stale_attempt(context):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    value = {'segments': [{'text': '원문'}]}
    assert checkpoint(repo, settings, job, 'TRANSCRIBE', {'v': 1}, lambda: value) == value
    def forbidden():
        raise AssertionError('cache must avoid computation')
    assert checkpoint(repo, settings, job, 'TRANSCRIBE', {'v': 1}, forbidden) == value
    repo.cancel(job['id'])
    with pytest.raises(Conflict):
        checkpoint(repo, settings, job, 'ALIGN', {'v': 2}, lambda: value)


def test_original_save_is_idempotent_and_preserves_owner_pointer(context):  # noqa: F811
    _, repo = context
    register(context)
    job = repo.claim()
    version = save_original(repo, job, {'text': 'original'})
    with repo.write() as connection:
        connection.execute(text("INSERT INTO transcript_versions VALUES ('owner-edit',:meeting,:parent,'user',:content,0)"),
                           {'meeting': job['meeting_id'], 'parent': version, 'content': '{"text":"owner"}'})
        connection.execute(text("UPDATE meetings SET transcript_version='owner-edit' WHERE id=:id"),
                           {'id': job['meeting_id']})
    assert save_original(repo, job, {'text': 'replacement'}) == version
    assert repo.meeting(job['meeting_id'])['transcript_version'] == 'owner-edit'
    with repo.engine.connect() as connection:
        rows = connection.execute(text('SELECT content_json FROM transcript_versions')).scalars().all()
    assert [json.loads(row) for row in rows] == [{'text': 'original'}, {'text': 'owner'}]


def test_corrupt_checkpoint_recomputed_and_cancel_during_calculation_not_published(context):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    checkpoint(repo, settings, job, 'TRANSCRIBE', {'v': 1}, lambda: {'text': 'first'})
    with repo.engine.connect() as connection:
        name = connection.execute(text('SELECT path FROM stage_artifacts')).scalar_one()
    (settings.data_dir / 'artifacts' / name).write_text('{"text":"corrupt"}')
    assert checkpoint(repo, settings, job, 'TRANSCRIBE', {'v': 1}, lambda: {'text': 'recomputed'}) == {'text': 'recomputed'}
    def cancelled():
        repo.cancel(job['id'])
        return {'text': 'too late'}
    with pytest.raises(Conflict):
        checkpoint(repo, settings, job, 'ALIGN', {'v': 1}, cancelled)
    assert not list((settings.data_dir / 'artifacts').glob('*align*'))


def test_transcript_api_auth_schema_conflict_and_undo(context):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    original = {'speakers': {'A': {'name': '화자 1', 'user_verified': False}},
                'segments': [{'id': 's1', 'text': '원문', 'speaker_id': 'A', 'start_ms': 0, 'end_ms': 1000}], 'merge_candidates': []}
    save_original(repo, job, original)
    path = f'/api/meetings/{job["meeting_id"]}/transcript'
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert client.get(path).status_code == 401
        headers = {'origin': settings.origin}
        login = client.post('/api/auth/login', headers=headers, json={'key': settings.owner_key_path.read_text()})
        headers['x-csrf-token'] = login.json()['csrf_token']
        current = client.get(path).json()
        payload = {'expected_revision': current['meeting_revision'],
                   'operation': {'type': 'utterance_text', 'utterance_id': current['reading']['utterances'][0]['id'], 'text': '교정문'}}
        assert client.post(path + '/edits', headers={'origin': settings.origin}, json=payload).status_code == 403
        edited = client.post(path + '/edits', headers=headers, json=payload)
        assert edited.status_code == 200 and edited.json()['reading']['utterances'][0]['text'] == '교정문'
        assert edited.json()['content']['segments'] == original['segments']
        assert repo.meetings('교정문') and not repo.meetings('원문')
        assert client.post(path + '/edits', headers=headers, json=payload).status_code == 409
        for operation in [{'type': 'merge', 'speaker_id': 'A', 'source_speaker_id': 'B'},
                          {'type': 'rename', 'speaker_id': 'A', 'name': '발표자'},
                          {'type': 'reassign', 'speaker_id': 'A', 'segment_ids': ['s1']}]:
            payload['operation'] = operation
            assert client.post(path + '/edits', headers=headers, json=payload).status_code == 422
        payload = {'expected_revision': edited.json()['meeting_revision'], 'operation': {'type': 'undo'}}
        restored = client.post(path + '/edits', headers=headers, json=payload)
        assert restored.status_code == 200 and restored.json()['content']['speakers'] == original['speakers']
        assert restored.json()['reading']['utterances'][0]['text'] == '원문'
        payload['expected_revision'] = restored.json()['meeting_revision']
        assert client.post(path + '/edits', headers=headers, json=payload).status_code == 409


def test_pipeline_stage_cache_only_invalidates_affected_models(context, monkeypatch):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    calls = {'asr': 0, 'align': 0, 'diarize': 0}
    class FakeModels:
        def __init__(self, settings):
            pass
        def audio(self, path):
            return []
        def transcribe(self, audio, language):
            calls['asr'] += 1
            return {'language': 'ko', 'segments': [], 'warnings': ['NO_RECOGNIZED_SPEECH']}
        def align(self, audio, transcript):
            calls['align'] += 1
            return {'segments': []}
        def diarize(self, audio, speakers):
            calls['diarize'] += 1
            return {'regular': [], 'exclusive': [], 'embeddings': {}}
    monkeypatch.setattr('meeting_minutes.speech_pipeline.SpeechModels', FakeModels)
    audio = settings.data_dir / 'test.wav'
    audio.write_bytes(b'fake audio boundary')
    assert run_speech(repo, settings, job, audio) == ('COMPLETED_TRANSCRIPT_ONLY', None)
    assert run_speech(repo, settings, job, audio) == ('COMPLETED_TRANSCRIPT_ONLY', None)
    assert calls == {'asr': 1, 'align': 1, 'diarize': 1}
    meeting = repo.meeting(job['meeting_id'])
    options = json.loads(meeting['settings_json'])
    options['speakers'] = 2
    with repo.write() as connection:
        connection.execute(text('UPDATE meetings SET settings_json=:settings WHERE id=:id'),
                           {'id': meeting['id'], 'settings': json.dumps(options)})
    run_speech(repo, settings, job, audio)
    assert calls == {'asr': 1, 'align': 1, 'diarize': 2}

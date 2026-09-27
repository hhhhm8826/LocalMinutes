import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.contracts import GenerateMinutes, Minutes
from meeting_minutes.minutes_management import edit_minutes, finish_transcript_only, queue_generation
from meeting_minutes.minutes_storage import save_minutes
from meeting_minutes.minutes_pipeline import run_minutes
from meeting_minutes.repository import Conflict
from meeting_minutes.speech_pipeline import save_original
from test_queue_media import context, register  # noqa: F401


def ready(ctx):
    _, repo = ctx
    register(ctx)
    job = repo.claim()
    version = save_original(repo, job, {'speakers': {'A': {'name': '화자 1'}}, 'segments': [
        {'id': 's1', 'text': '점검을 진행합시다.', 'start_ms': 0, 'end_ms': 1000, 'speaker_id': 'A'}]})
    repo.finish(job['id'], job['attempt_id'], 'COMPLETED_TRANSCRIPT_ONLY')
    return repo.meeting(job['meeting_id']), version


def complete(repo, job, version):
    current = repo.claim()
    assert current['id'] == job['id']
    content = Minutes(meeting_id=job['meeting_id'], transcript_version=version, revision=1, summary='점검을 진행하기로 했다.',
        topics=[], decisions=[{'id': 'd1', 'text': '점검 진행', 'source_segment_ids': ['s1']}],
        action_items=[], open_questions=[], review_notes=[])
    saved = save_minutes(repo, current, content, None)
    repo.finish(current['id'], current['attempt_id'], 'COMPLETED')
    return saved


def test_generation_http_idempotency_reuse_and_explicit_new_draft(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        headers = {'origin': settings.origin}
        headers['x-csrf-token'] = client.post('/api/auth/login', headers=headers,
            json={'key': settings.owner_key_path.read_text()}).json()['csrf_token']
        path = f'/api/meetings/{meeting["id"]}/minutes/generate'
        body = {'expected_revision': meeting['revision'], 'transcript_version': version, 'allow_external_text': True}
        headers['idempotency-key'] = 'request-0001'
        queued = client.post(path, json=body, headers=headers)
        assert queued.status_code == 202
        assert client.post(path, json=body, headers=headers).json()['id'] == queued.json()['id']
        headers['idempotency-key'] = 'request-alias'
        body['expected_revision'] = repo.meeting(meeting['id'])['revision']
        assert client.post(path, json=body, headers=headers).json()['id'] == queued.json()['id']
        body['new_draft'] = True
        assert client.post(path, json=body, headers=headers).status_code == 409
        body['new_draft'] = False
        complete(repo, queued.json(), version)
        assert client.post(path, json=body, headers=headers).status_code == 200
        headers['idempotency-key'] = 'new-draft-0001'
        body.update(new_draft=True, expected_revision=repo.meeting(meeting['id'])['revision'])
        regenerated = client.post(path, json=body, headers=headers)
        assert regenerated.status_code == 202 and regenerated.json()['id'] != queued.json()['id']
        done = client.post(f'/api/jobs/{regenerated.json()["id"]}/finish-transcript-only', headers=headers)
        assert done.status_code == 200 and done.json()['state'] == 'COMPLETED_TRANSCRIPT_ONLY'


def test_edit_confirm_versions_evidence_and_stale_request(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'edit-test-job')
    original = complete(repo, job, version)
    revision = repo.meeting(meeting['id'])['revision']
    with pytest.raises(Conflict, match='REVIEW_REQUIRED'):
        edit_minutes(repo, meeting['id'], original['id'], revision, confirm=True)
    content = Minutes.model_validate(original['content'])
    content.decisions[0].review_status = 'verified'
    edited = edit_minutes(repo, meeting['id'], original['id'], revision, content)
    with pytest.raises(Conflict, match='REVISION_CONFLICT'):
        edit_minutes(repo, meeting['id'], original['id'], revision, content)
    confirmed = edit_minutes(repo, meeting['id'], edited['id'], edited['meeting_revision'], confirm=True)
    assert confirmed['content']['status'] == 'confirmed'
    with repo.engine.connect() as connection:
        old = json.loads(connection.execute(text('SELECT content_json FROM minutes_revisions WHERE id=:id'),
                                           {'id': original['id']}).scalar_one())
    assert old['status'] == 'draft' and old['decisions'][0]['review_status'] == 'needs_review'


def test_running_job_cannot_be_finished_without_process_stop(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'running-test-job')
    repo.claim()
    with pytest.raises(Conflict, match='TRANSCRIPT_ONLY_NOT_AVAILABLE'):
        finish_transcript_only(repo, job['id'])


def test_minutes_edit_http_csrf_large_valid_body_and_invalid_evidence(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'large-edit-job')
    original = complete(repo, job, version)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        headers = {'origin': settings.origin}
        csrf = client.post('/api/auth/login', headers=headers, json={'key': settings.owner_key_path.read_text()}).json()['csrf_token']
        body = {'expected_revision': repo.meeting(meeting['id'])['revision'], 'content': original['content']}
        path = f'/api/meetings/{meeting["id"]}/minutes/{original["id"]}'
        assert client.patch(path, headers=headers, json=body).status_code == 403
        headers['x-csrf-token'] = csrf
        body['content']['summary'] = '가' * 20000
        body['content']['decisions'][0]['text'] = '나' * 5000
        edited = client.patch(path, headers=headers, json=body)
        assert edited.status_code == 200
        invalid = {'expected_revision': edited.json()['meeting_revision'], 'content': edited.json()['content']}
        invalid['content']['decisions'][0]['source_segment_ids'] = ['missing']
        path = f'/api/meetings/{meeting["id"]}/minutes/{edited.json()["id"]}'
        assert client.patch(path, headers=headers, json=invalid).status_code == 422
        assert len(client.get(f'/api/meetings/{meeting["id"]}/minutes/versions').json()) == 2


def test_queued_generation_rejects_changed_model_configuration(context, monkeypatch):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'config-change-job')
    job = repo.claim()
    settings.codex_model = 'different-model'
    def forbidden(*args):
        raise AssertionError('must not silently substitute a different model')
    monkeypatch.setattr('meeting_minutes.minutes_pipeline.CodexCliProvider', forbidden)
    assert run_minutes(repo, settings, job) == ('BLOCKED', 'CODEX_GENERATION_CONFIG_CHANGED')

import json

from sqlalchemy import text

from meeting_minutes.codex_provider import CodexFailure
from meeting_minutes.minutes_pipeline import run_minutes
from meeting_minutes.job_runner import run
from meeting_minutes.speech_pipeline import save_original
from test_queue_media import context, register  # noqa: F401


def test_failed_summary_preserves_transcript_and_retry_only_calls_minutes(context, monkeypatch):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    meeting = repo.meeting(job['meeting_id'])
    options = json.loads(meeting['settings_json'])
    options['allow_external_text'] = True
    with repo.write() as connection:
        connection.execute(text('UPDATE meetings SET settings_json=:settings WHERE id=:id'),
                           {'id': meeting['id'], 'settings': json.dumps(options)})
    version = save_original(repo, job, {'speakers': {}, 'segments': []})
    calls = []
    class Provider:
        def __init__(self, settings):
            pass
        def generate(self, payload, schema, reserve):
            reserve()
            calls.append(payload)
            if len(calls) == 1:
                raise CodexFailure('CODEX_TIMEOUT')
            return {'meeting_id': payload['meeting_id'], 'transcript_version': payload['transcript_version'],
                    'revision': payload['revision'], 'summary': '검토할 발언이 없습니다.',
                    'topics': [], 'decisions': [], 'action_items': [], 'open_questions': [], 'review_notes': []}, {'usage': {}}
    monkeypatch.setattr('meeting_minutes.minutes_pipeline.CodexCliProvider', Provider)
    state, reason = run_minutes(repo, settings, job)
    assert (state, reason) == ('FAILED', 'CODEX_TIMEOUT')
    assert repo.meeting(meeting['id'])['transcript_version'] == version
    repo.finish(job['id'], job['attempt_id'], state, reason)
    repo.retry(job['id'])
    retry = repo.claim()
    # The original input no longer exists; a summary retry must not decode media or run speech again.
    media = repo.media(job['media_id'])
    (settings.data_dir / 'media' / media['stored_name']).unlink()
    assert run(repo, settings, retry) == ('COMPLETED', None)
    assert calls[0] == calls[1]
    assert run_minutes(repo, settings, retry) == ('COMPLETED', None)
    assert len(calls) == 2
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM minutes_revisions')).scalar_one() == 1


def test_no_consent_cannot_reach_provider(context, monkeypatch):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    save_original(repo, job, {'speakers': {}, 'segments': []})
    def forbidden(*args):
        raise AssertionError('must not call external provider')
    monkeypatch.setattr('meeting_minutes.minutes_pipeline.CodexCliProvider', forbidden)
    assert run_minutes(repo, settings, job) == ('BLOCKED', 'EXTERNAL_TEXT_NOT_ALLOWED')

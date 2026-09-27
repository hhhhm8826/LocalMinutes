import json

import pytest
from sqlalchemy import text

from meeting_minutes.codex_provider import CodexFailure
from meeting_minutes.contracts import Minutes
from meeting_minutes.minutes_storage import finish_call, reserve_call, save_minutes
from meeting_minutes.speech_pipeline import save_original
from test_queue_media import context, register  # noqa: F401


def test_call_budget_survives_retry_and_never_stores_prompt(context):  # noqa: F811
    _, repo = context
    register(context)
    job = repo.claim()
    call = reserve_call(repo, job, 1, 'fingerprint')
    finish_call(repo, call, 'FAILED', {'prompt': 'SECRET', 'usage': {'input_tokens': 12}})
    repo.finish(job['id'], job['attempt_id'], 'BLOCKED', 'CODEX_NETWORK')
    repo.retry(job['id'])
    retried = repo.claim()
    with pytest.raises(CodexFailure, match='BUDGET_EXHAUSTED'):
        reserve_call(repo, retried, 1, 'fingerprint')
    with repo.engine.connect() as connection:
        metrics = connection.execute(text('SELECT metrics_json FROM usage_records')).scalar_one()
    assert 'SECRET' not in metrics and json.loads(metrics)['call_reserved']


def test_minutes_save_idempotent_and_preserves_new_transcript_pointer(context):  # noqa: F811
    _, repo = context
    register(context)
    job = repo.claim()
    version = save_original(repo, job, {'segments': [], 'speakers': {}})
    with repo.write() as connection:
        connection.execute(text("INSERT INTO transcript_versions VALUES ('newer',:meeting,:parent,'user','{}',0)"),
                           {'meeting': job['meeting_id'], 'parent': version})
        connection.execute(text("UPDATE meetings SET transcript_version='newer' WHERE id=:id"), {'id': job['meeting_id']})
    result = Minutes(meeting_id=job['meeting_id'], transcript_version=version, revision=1, summary='이전 버전 요약',
                     topics=[], decisions=[], action_items=[], open_questions=[], review_notes=[])
    first = save_minutes(repo, job, result, None)
    assert save_minutes(repo, job, result, None)['id'] == first['id']
    assert repo.meeting(job['meeting_id'])['minutes_revision'] is None
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM minutes_revisions')).scalar_one() == 1

import pytest

from meeting_minutes.codex_provider import CodexFailure
from meeting_minutes.minutes_calls import call_with_retries
from test_queue_media import context, register  # noqa: F401


@pytest.mark.parametrize('error,expected_calls', [('CODEX_NETWORK', 3), ('MINUTES_SCHEMA_INVALID', 2), ('CODEX_USAGE_LIMIT', 1)])
def test_retry_caps_are_durable_and_use_exponential_backoff(context, monkeypatch, error, expected_calls):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    delays, calls = [], []
    monkeypatch.setattr('meeting_minutes.minutes_calls.backoff', lambda repo, job, seconds: delays.append(seconds))
    class Provider:
        def generate(self, payload, schema, reserve):
            reserve()
            calls.append(payload)
            raise CodexFailure(error)
    with pytest.raises(CodexFailure, match=error):
        call_with_retries(repo, settings, job, Provider(), {}, {}, lambda value: value, 'input-hash')
    assert len(calls) == expected_calls
    assert delays == ([1, 2] if error == 'CODEX_NETWORK' else [])
    # A later explicit retry may make a fresh initial call, but cannot reset automatic repair/retry allowances.
    with pytest.raises(CodexFailure, match=error):
        call_with_retries(repo, settings, job, Provider(), {}, {}, lambda value: value, 'input-hash')
    assert len(calls) == expected_calls + 1


def test_network_then_schema_repair_can_succeed_without_losing_payload(context, monkeypatch):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    monkeypatch.setattr('meeting_minutes.minutes_calls.backoff', lambda *args: None)
    calls = []
    class Provider:
        def generate(self, payload, schema, reserve):
            reserve()
            calls.append(payload)
            if len(calls) == 1:
                raise CodexFailure('CODEX_NETWORK')
            if len(calls) == 2:
                raise CodexFailure('MINUTES_SCHEMA_INVALID')
            return {'ok': True}, {'usage': {}}
    result = call_with_retries(repo, settings, job, Provider(), {'segments': ['source']}, {}, lambda value: value, 'hash')
    assert result == {'ok': True} and all(value['segments'] == ['source'] for value in calls)


def test_pre_generation_failure_never_loops_without_a_reservation(context):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.claim()
    calls = []
    class Provider:
        def generate(self, *args):
            calls.append(True)
            raise CodexFailure('CODEX_NETWORK')
    with pytest.raises(CodexFailure, match='CODEX_NETWORK'):
        call_with_retries(repo, settings, job, Provider(), {}, {}, lambda value: value, 'hash')
    assert len(calls) == 1


@pytest.mark.parametrize('code,delay,expected_calls', [('AI_RATE_LIMIT',7,1),
    ('AI_SERVICE_UNAVAILABLE',7,3), ('AI_SERVICE_UNAVAILABLE',60,1)])
def test_gemini_diagnostics_persist_and_retry_wait_is_bounded(context,monkeypatch,code,delay,expected_calls):  # noqa: F811
    import json
    from sqlalchemy import text
    from meeting_minutes.ai_snapshot import configuration
    from meeting_minutes.ai_runtime import GenerationRuntime
    settings,repo=context
    register(context)
    config=configuration(settings,{},'gemini_api','gemini-3.5-flash')
    with repo.write() as c:
        c.execute(text('UPDATE jobs SET ai_config_json=:raw'),{'raw':config.model_dump_json()})
    job=repo.claim()
    calls=[]
    delays=[]
    monkeypatch.setattr('meeting_minutes.minutes_calls.backoff',lambda r,j,s:delays.append(s))
    class Provider:
        def generate(self,payload,schema,reserve):
            reserve()
            calls.append(True)
            raise CodexFailure(code,{'category':'http_api','http_status':429 if code=='AI_RATE_LIMIT' else 503,
                                     'retry_after_seconds':delay,'message':'FAKE-SECRET'})
    with pytest.raises(CodexFailure,match=code):
        call_with_retries(repo,settings,job,Provider(),{},{},lambda v:v,'hash',runtime=GenerationRuntime(settings,config))
    assert len(calls)==expected_calls
    assert delays==([7,7] if expected_calls==3 else [])
    with repo.engine.connect() as c:
        records=c.execute(text('SELECT metrics_json FROM usage_records')).scalars().all()
    assert len(records)==expected_calls
    for raw in records:
        assert 'FAKE-SECRET' not in raw
        metrics=json.loads(raw)
        assert metrics['diagnostic']['retry_after_seconds']==delay and metrics['status']=='FAILED'

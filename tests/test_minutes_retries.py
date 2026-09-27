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

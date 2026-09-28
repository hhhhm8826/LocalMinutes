import json

import pytest
from sqlalchemy import text

from meeting_minutes.ai_common import AIFailure
from meeting_minutes.ai_snapshot import capture, codex_settings, parse
from meeting_minutes.contracts import GenerateMinutes, MeetingCreate
from meeting_minutes.minutes_identity import generation_identity
from meeting_minutes.minutes_management import queue_generation
from meeting_minutes.minutes_pipeline import run_minutes
from meeting_minutes.minutes_storage import reserve_call
from meeting_minutes.youtube_jobs import register_youtube
from test_minutes_management import ready
from test_queue_media import context, register  # noqa: F401


def switch(repo, provider):
    with repo.write() as connection:
        connection.execute(text('UPDATE ai_policy SET active_provider=:provider,revision=revision+1'), {'provider': provider})


def test_upload_idempotency_and_new_jobs_pin_policy(context):  # noqa: F811
    settings, repo = context
    settings.codex_max_calls = 7
    first = register(context)
    original = json.loads(first['ai_config_json'])
    assert (original['provider'], original['model'], original['max_calls']) == ('codex_cli', 'gpt-6-astra', 7)
    switch(repo, 'gemini_api')
    duplicate, created = repo.register_media(first['meeting_id'], 'sample.wav', 'ignored', 1, 'ignored',
        first['idempotency_key'], first['request_hash'], settings=settings)
    assert not created and duplicate['ai_config_json'] == first['ai_config_json']
    second = register(context)
    current = json.loads(second['ai_config_json'])
    assert current['provider'] == 'gemini_api' and current['policy_revision'] == 2
    assert not any('key' in name or 'credential' in name for name in current)
    config = parse(second['ai_config_json'], {})
    with pytest.raises(AIFailure, match='AI_ADAPTER_NOT_READY'):
        codex_settings(settings, config)


def test_youtube_retry_keeps_registered_config(context):  # noqa: F811
    settings, repo = context
    first = register_youtube(repo, 'https://www.youtube.com/watch?v=abcdefghijk', '', 'youtube-0001', settings=settings)
    switch(repo, 'claude_cli')
    duplicate = register_youtube(repo, 'https://www.youtube.com/watch?v=abcdefghijk', '', 'youtube-0001', settings=settings)
    assert duplicate['id'] == first['id'] and duplicate['ai_config_json'] == first['ai_config_json']
    second = register_youtube(repo, 'https://www.youtube.com/watch?v=abcdefghijk', '', 'youtube-0002', settings=settings)
    assert json.loads(second['ai_config_json'])['provider'] == 'claude_cli'
    assert parse(second['ai_config_json'], {'document_kind': 'video_summary'}).schema_hash
    with pytest.raises(AIFailure, match='AI_GENERATION_CONFIG_CHANGED'):
        parse(second['ai_config_json'], {'document_kind': 'meeting'})


def test_regeneration_idempotency_and_retry_keep_original_budget(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    request = GenerateMinutes(expected_revision=meeting['revision'], transcript_version=version, allow_external_text=True)
    first = queue_generation(repo, settings, meeting['id'], request, 'generation-0001')
    current = repo.claim()
    reserve_call(repo, current, 10, 'fake-call')
    repo.finish(current['id'], current['attempt_id'], 'FAILED', 'TEST_FAILURE')
    switch(repo, 'gemini_api')
    settings.codex_max_calls = 30
    duplicate = queue_generation(repo, settings, meeting['id'], request, 'generation-0001')
    assert duplicate['id'] == first['id'] and duplicate['ai_config_json'] == first['ai_config_json']
    repo.retry(first['id'])
    retry = repo.claim()
    assert retry['ai_config_json'] == first['ai_config_json']
    from meeting_minutes.minutes_long import remaining_calls
    assert remaining_calls(repo, retry, json.loads(first['ai_config_json'])['max_calls']) == 9
    repo.finish(retry['id'], retry['attempt_id'], 'FAILED', 'TEST_FAILURE')
    request.expected_revision = repo.meeting(meeting['id'])['revision']
    second = queue_generation(repo, settings, meeting['id'], request, 'generation-0002')
    assert json.loads(second['ai_config_json'])['provider'] == 'gemini_api'
    assert json.loads(second['request_json'])['generation_key'] != json.loads(first['request_json'])['generation_key']


def test_identity_covers_provider_revision_and_execution_limits(context):  # noqa: F811
    settings, repo = context
    with repo.write() as connection:
        config = capture(connection, settings, {})
    identity = generation_identity('transcript', {}, settings, config)
    for field, value in [('provider', 'gemini_api'), ('policy_revision', 2), ('max_calls', 11), ('timeout_seconds', 200)]:
        assert generation_identity('transcript', {}, settings, config.model_copy(update={field: value})) != identity
    settings.codex_model = 'new-default'
    assert generation_identity('transcript', {}, settings, config) == identity
    with pytest.raises(AIFailure, match='AI_CONFIG_INVALID'):
        parse(json.dumps(dict(config.model_dump(), api_key='FAKE-ONLY')), {})
    with pytest.raises(AIFailure, match='AI_GENERATION_CONFIG_CHANGED'):
        parse(config.model_copy(update={'adapter_version': 999}).model_dump_json(), {})


def test_unrecoverable_legacy_job_is_blocked_without_external_calls(context, monkeypatch):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    request = GenerateMinutes(expected_revision=meeting['revision'], transcript_version=version, allow_external_text=True)
    job = queue_generation(repo, settings, meeting['id'], request, 'legacy-job-0001')
    with repo.write() as connection:
        connection.execute(text('UPDATE jobs SET ai_config_json=NULL WHERE id=:id'), {'id': job['id']})
    def forbidden(*args):
        raise AssertionError('legacy ambiguity must not call a provider')
    monkeypatch.setattr('meeting_minutes.ai_runtime.CodexCliProvider', forbidden)
    current = repo.claim()
    assert run_minutes(repo, settings, current) == ('BLOCKED', 'AI_LEGACY_CONFIG_REQUIRED')
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM usage_records')).scalar_one() == 0


def test_snapshot_failure_rolls_back_media_registration(context, monkeypatch):  # noqa: F811
    settings, repo = context
    meeting = repo.create_meeting(MeetingCreate(title='rollback'))
    def fail(*args):
        raise RuntimeError('injected failure')
    monkeypatch.setattr('meeting_minutes.ai_snapshot.capture', fail)
    with pytest.raises(RuntimeError, match='injected failure'):
        repo.register_media(meeting['id'], 'a.wav', 'a.wav', 1, 'hash', 'rollback-0001', 'hash', settings=settings)
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM media_assets')).scalar_one() == 0
        assert connection.execute(text('SELECT COUNT(*) FROM jobs')).scalar_one() == 0


def test_context_limits_are_pinned_despite_catalog_changes(context):  # noqa: F811
    from meeting_minutes.ai_runtime import GenerationRuntime, create_provider
    settings, repo = context
    job = register(context)
    config = parse(job['ai_config_json'], {})
    runtime = GenerationRuntime(settings, config)
    original = runtime.budget({'type': 'object'})
    assert original['effective_context_tokens'] == 272000 * 95 // 100
    path = settings.codex_home / 'models_cache.json'
    path.write_text(json.dumps({'models': [{'slug': settings.codex_model, 'context_window': 180000}]}))
    assert runtime.budget({'type': 'object'}) == original
    new = parse(register(context)['ai_config_json'], {})
    assert new.capabilities.context_tokens == 180000
    path.unlink()
    assert runtime.budget({'type': 'object'}) == original
    assert create_provider(runtime).runtime is runtime
    # Input registration survives missing metadata; generation never guesses limits.
    missing = parse(register(context)['ai_config_json'], {})
    assert missing.capabilities is None
    with pytest.raises(AIFailure, match='AI_MODEL_METADATA_REQUIRED'):
        GenerationRuntime(settings, missing).budget({})
    assert config.capabilities.output_tokens == 65536
    with pytest.raises(AIFailure, match='AI_CONFIG_INVALID'):
        parse(config.model_copy(update={'provider': 'gemini_api'}).model_dump_json(), {})


def test_non_codex_capabilities_do_not_require_codex_catalog(context):  # noqa: F811
    from meeting_minutes.ai_runtime import GenerationRuntime
    from meeting_minutes.ai_snapshot import configuration
    settings, _ = context
    (settings.codex_home / 'models_cache.json').unlink()
    for provider, model, window, output in [('gemini_api','gemini-3.5-flash',1048576,65536),
                                           ('claude_cli','claude-sonnet-4-6',200000,32000)]:
        config = configuration(settings, {}, provider, model)
        assert config.capabilities.context_tokens == window
        assert GenerationRuntime(settings, parse(config.model_dump_json(), {})).budget({})['output_reserve_tokens'] == output
        changed = config.model_copy(update={'capabilities': config.capabilities.model_copy(update={'output_tokens': output - 1})})
        assert generation_identity('transcript', {}, settings, changed) != generation_identity('transcript', {}, settings, config)


def test_codex_adapter_uses_registered_budget_without_catalog(context, monkeypatch):  # noqa: F811
    from meeting_minutes.ai_runtime import GenerationRuntime, create_provider
    settings, _ = context
    job = register(context)
    runtime = GenerationRuntime(settings, parse(job['ai_config_json'], {}))
    (settings.codex_home / 'models_cache.json').unlink()
    provider = create_provider(runtime)
    monkeypatch.setattr(provider, 'preflight', lambda *args: None)
    calls = []
    def execute(argv, **kwargs):
        kwargs['result_path'].write_text('{"summary":"가상 요약"}')
        return 0, b'{"type":"turn.completed","usage":{"output_tokens":20}}', b''
    monkeypatch.setattr('meeting_minutes.codex_provider.bounded_cli', execute)
    value, metrics = provider.generate({'segments': []}, {'type':'object','properties':{'summary':{'type':'string'}}}, lambda: calls.append(True))
    assert value == {'summary':'가상 요약'} and calls == [True]
    assert metrics['context_budget']['effective_context_tokens'] == 258400


def test_gemini_json_adapter_has_new_identity(context):  # noqa: F811
    from meeting_minutes.ai_snapshot import configuration
    settings, repo = context
    register(context)
    switch(repo, 'gemini_api')
    config = parse(register(context)['ai_config_json'], {})
    assert config.model == 'gemini-3.8-flash' and config.adapter_version == 3
    assert config.capabilities.source == 'gemini-3.8-flash-v1'
    with pytest.raises(AIFailure, match='AI_GENERATION_CONFIG_CHANGED'):
        parse(config.model_copy(update={'adapter_version': 2}).model_dump_json(), {})
    assert configuration(settings, {}, 'gemini_api', 'gemini-3.5-flash').capabilities

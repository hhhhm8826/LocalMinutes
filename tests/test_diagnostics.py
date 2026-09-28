import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.diagnostics import app_usage, runtime_status
from test_queue_media import context, register  # noqa: F401


def test_runtime_status_never_returns_auth_output_or_uses_generation(context, monkeypatch):  # noqa: F811
    settings, _ = context
    settings.codex_cli = settings.config_dir / 'codex'
    settings.codex_cli.write_text('fixture')
    settings.codex_user_home = settings.config_dir / 'clean-home'
    settings.codex_user_home.mkdir()
    monkeypatch.setenv('OPENAI_API_KEY', 'private-secret')
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv[1:])
        assert 'OPENAI_API_KEY' not in kwargs['env']
        assert kwargs['env']['CODEX_HOME'] == str(settings.codex_home)
        if argv[-1] == '--version':
            return 0, b'codex-cli 0.157.1\n', b''
        return 0, b'', b'Logged in using ChatGPT private-auth-canary'
    monkeypatch.setattr('meeting_minutes.diagnostics.bounded_cli', runner)
    result = runtime_status(settings)
    assert result['login'] == 'chatgpt' and result['version'] == '0.157.1'
    assert result['configured_model_in_local_catalog'] is True
    assert result['account_remaining_quota'] is None
    assert calls == [['--version'], ['login', 'status']]
    assert 'private' not in json.dumps(result)


def test_diagnostics_auth_cache_and_observed_usage(context, monkeypatch):  # noqa: F811
    settings, repo = context
    register(context)
    job = repo.jobs()[0]
    with repo.write() as connection:
        connection.execute(text('INSERT INTO usage_records VALUES (:id,:job,:attempt,:stage,:metrics,:now)'),
            {'id': 'usage', 'job': job['id'], 'attempt': job['attempt_id'], 'stage': 'SUMMARIZE', 'now': 1,
             'metrics': json.dumps({'call_reserved': True, 'completed': True, 'model': 'observed-model',
                                   'usage': {'input_tokens': 20, 'cached_input_tokens': 3, 'output_tokens': 4}})})
    usage = app_usage(repo)
    assert (usage['reserved_calls'], usage['completed_calls'], usage['input_tokens']) == (1, 1, 20)
    assert usage['last_observed_model'] == 'observed-model'
    import time
    calls = []
    def diagnostic(*_):
        calls.append(1)
        return {'checked_at': time.time(), 'account_remaining_quota': None}
    monkeypatch.setattr('meeting_minutes.api.runtime_status', diagnostic)
    claude_calls = []
    def claude_diagnostic(*_):
        claude_calls.append(1)
        return {'login':'not_logged_in'}
    monkeypatch.setattr('meeting_minutes.api.claude_runtime_status', claude_diagnostic)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert client.get('/api/diagnostics').status_code == 401
        assert not calls
        client.post('/api/auth/login', headers={'origin': settings.origin}, json={'key': settings.owner_key_path.read_text()})
        result = client.get('/api/diagnostics').json()
        assert result['database_revision'] == '0017'
        assert result['usage']['output_tokens'] == 4
        assert result['model_cache']['inference_checked'] is False
        assert 'owner-key' not in json.dumps(result)
        assert client.get('/api/diagnostics').status_code == 200
        assert len(calls) == 1 and len(claude_calls) == 1
        assert result['claude_runtime']['login'] == 'not_logged_in'


def test_claude_diagnostics_isolated_and_auth_output_private(context, monkeypatch):  # noqa: F811
    from meeting_minutes.claude_diagnostics import runtime_status as claude_status
    from test_claude_provider import runtime_for
    settings,_ = context
    runtime_for(context,monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY','private-key')
    calls = []
    def run(argv, **options):
        calls.append(argv[1:])
        assert 'ANTHROPIC_API_KEY' not in options['env']
        assert options['env']['CLAUDE_CONFIG_DIR'] == str(settings.claude_home)
        if '--version' in argv:
            return 0,b'2.1.283 (Claude Code)',b''
        return 0,json.dumps({'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','email':'private-canary','token':'private-key'}).encode(),b'private-stderr'
    monkeypatch.setattr('meeting_minutes.claude_diagnostics.bounded_cli',run)
    result = claude_status(settings)
    assert result['login'] == 'claude_subscription' and result['version'] == '2.1.283'
    assert calls == [['--version'],['auth','status']]
    assert 'private' not in json.dumps(result)

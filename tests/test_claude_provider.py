import json

import pytest

from meeting_minutes.ai_common import AIFailure
from meeting_minutes.ai_runtime import GenerationRuntime, create_provider
from meeting_minutes.ai_snapshot import configuration
from meeting_minutes.claude_provider import FLAGS, VERSION, LIMIT_ENV
from test_queue_media import context  # noqa: F401

SCHEMA = {'type':'object','properties':{'summary':{'type':'string'}},'required':['summary'],'additionalProperties':False}


def runtime_for(ctx, monkeypatch):
    settings, _ = ctx
    settings.claude_cli = settings.data_dir/'fake-claude'
    settings.claude_cli.write_bytes(b'--max-turns <turns> --system-prompt-file <file> ' + ' '.join(LIMIT_ENV).encode())
    settings.claude_home = settings.config_dir/'claude-home'
    settings.claude_user_home = settings.config_dir/'claude-user-home'
    for root in (settings.claude_home, settings.claude_user_home):
        root.mkdir(mode=0o700)
    monkeypatch.setattr('meeting_minutes.claude_provider.MANAGED_ROOT',settings.config_dir/'no-managed-settings')
    config = configuration(settings, {}, 'claude_cli', 'claude-sonnet-4-6')
    return GenerationRuntime(settings,config)


def fake_cli(handler, auth=None):
    def run(argv, **kwargs):
        if '--version' in argv:
            return 0,(VERSION+' (Claude Code)').encode(),b''
        if '--help' in argv:
            return 0,' '.join(FLAGS).encode(),b''
        if 'status' in argv:
            return 0,json.dumps(auth or {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}).encode(),b''
        return handler(argv,kwargs)
    return run


def good():
    return {'type':'result','subtype':'success','is_error':False,'structured_output':{'summary':'검토 결과입니다.'},
            'modelUsage':{'claude-sonnet-4-6':{}},'usage':{'input_tokens':10,'output_tokens':20}}


def test_claude_isolated_stdin_structured_result_and_cleanup(context, monkeypatch):  # noqa: F811
    runtime = runtime_for(context,monkeypatch)
    for key in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','GOOGLE_API_KEY','HTTPS_PROXY'):
        monkeypatch.setenv(key,'FAKE-INHERITED-SECRET')
    reserved,cwds = [],[]
    def generate(argv, opts):
        assert reserved == [True]
        assert '-p' in argv and '--bare' not in argv and '--dangerously-skip-permissions' not in argv
        assert argv[argv.index('--tools')+1] == ''
        assert argv[argv.index('--max-turns')+1] == '1'
        assert argv[argv.index('--permission-prompts')+1] == 'none'
        assert '--no-session-persistence' in argv and '--restricted' in argv and '--safe-mode' in argv
        assert 'private transcript' not in repr(argv)+repr(opts['env'])
        assert b'private transcript' in opts['input_bytes']
        assert 'FAKE-INHERITED-SECRET' not in repr(opts['env'])
        assert opts['env']['CLAUDE_CODE_MAX_RETRIES'] == '0'
        assert opts['env']['MAX_STRUCTURED_OUTPUT_RETRIES'] == '1'
        assert opts['env']['CLAUDE_CODE_DISABLE_TERMINAL_TITLE'] == '1'
        assert '--fallback-model' not in argv
        assert argv[argv.index('--setting-sources')+1] == ''
        assert opts['env']['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] == '32000'
        cwds.append(opts['cwd'])
        return 0,json.dumps(good()).encode(),b''
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',fake_cli(generate))
    runtime.settings.codex_home.joinpath('models_cache.json').unlink()
    value,metrics = create_provider(runtime).generate({'segments':[],'note':'private transcript'},SCHEMA,lambda:reserved.append(True))
    assert value['summary'] == '검토 결과입니다.' and metrics['actual_model'] == runtime.model
    assert all(not cwd.exists() for cwd in cwds)


@pytest.mark.parametrize('mutation,code',[
    ({'is_error':True},'AI_INCOMPLETE_OUTPUT'),({'subtype':'error_max_turns'},'AI_INCOMPLETE_OUTPUT'),
    ({'permission_denials':[{'tool_name':'Bash'}]},'AI_UNEXPECTED_TOOL'),
    ({'structured_output':None},'AI_EMPTY_OUTPUT'),({'structured_output':{'wrong':True}},'MINUTES_SCHEMA_INVALID'),
    ({'modelUsage':{'claude-opus-other':{}}},'AI_UNEXPECTED_MODEL')])
def test_claude_rejects_failed_or_wrong_model_result(context,monkeypatch,mutation,code):  # noqa: F811
    runtime=runtime_for(context,monkeypatch)
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',fake_cli(lambda *args:(0,json.dumps(good()|mutation).encode(),b'')))
    with pytest.raises(AIFailure) as caught:
        create_provider(runtime).generate({'segments':[]},SCHEMA,lambda:None)
    assert caught.value.code == code


def test_claude_rejects_api_billing_and_managed_customizations(context,monkeypatch):  # noqa: F811
    runtime=runtime_for(context,monkeypatch)
    def forbidden(*args):
        pytest.fail('unsafe auth cannot generate')
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',fake_cli(forbidden,{'loggedIn':True,'authMethod':'api_key'}))
    with pytest.raises(AIFailure,match='AI_SUBSCRIPTION_REQUIRED'):
        create_provider(runtime).generate({'segments':[]},SCHEMA,forbidden)
    (runtime.settings.claude_home/'managed-settings.json').write_text('{}')
    with pytest.raises(AIFailure,match='AI_ISOLATION_UNVERIFIED'):
        create_provider(runtime).generate({'segments':[]},SCHEMA,forbidden)


def test_actual_model_not_carried_between_calls_and_multiple_models_rejected(context, monkeypatch):  # noqa: F811
    runtime = runtime_for(context, monkeypatch)
    outputs = [good(), {**good(), 'modelUsage': {}}, {**good(), 'modelUsage': {'claude-sonnet-4-6':{}, 'unexpected-model':{}}}]
    def generate(argv, opts):
        return 0, json.dumps(outputs.pop(0)).encode(), b''
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli', fake_cli(generate))
    provider = create_provider(runtime)
    assert provider.generate({'segments':[]},SCHEMA,lambda:None)[1]['actual_model'] == 'claude-sonnet-4-6'
    with pytest.raises(AIFailure, match='AI_UNEXPECTED_MODEL'):
        provider.generate({'segments':[]},SCHEMA,lambda:None)
    assert provider.actual_model is None
    with pytest.raises(AIFailure, match='AI_UNEXPECTED_MODEL'):
        provider.generate({'segments':[]},SCHEMA,lambda:None)


@pytest.mark.parametrize('usage, code', [
    ({}, 'AI_PROTOCOL_ERROR'),
    ({'output_tokens': True}, 'AI_PROTOCOL_ERROR'),
    ({'output_tokens': -1}, 'AI_PROTOCOL_ERROR'),
])
def test_claude_rejects_unverified_or_excessive_usage(context, monkeypatch, usage, code):  # noqa: F811
    runtime = runtime_for(context, monkeypatch)
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',
        fake_cli(lambda *args: (0, json.dumps(good() | {'usage': usage}).encode(), b'')))
    provider = create_provider(runtime)
    with pytest.raises(AIFailure, match=code):
        provider.generate({'segments': []}, SCHEMA, lambda: None)
    assert provider.actual_model is None


def test_claude_missing_limit_support_blocks_before_auth_or_reservation(context, monkeypatch):  # noqa: F811
    runtime = runtime_for(context, monkeypatch)
    runtime.settings.claude_cli.write_bytes(b'--max-turns <turns> --system-prompt-file <file>')
    def run(argv, **kwargs):
        if '--version' in argv:
            return 0, (VERSION + ' (Claude Code)').encode(), b''
        if '--help' in argv:
            return 0, ' '.join(FLAGS).encode(), b''
        pytest.fail('unsupported limits must stop before auth or generation')
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli', run)
    with pytest.raises(AIFailure, match='AI_ISOLATION_UNVERIFIED'):
        create_provider(runtime).generate({'segments': []}, SCHEMA,
            lambda: pytest.fail('must not reserve an external call'))


def test_claude_preserves_completed_output_above_context_reserve(context, monkeypatch):  # noqa: F811
    runtime = runtime_for(context, monkeypatch)
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',
        fake_cli(lambda *args: (0, json.dumps(good() | {'usage': {'input_tokens':10,'output_tokens':32001}}).encode(), b'')))
    value, metrics = create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)
    assert value['summary'] and metrics['usage']['output_tokens'] == 32001


def test_opus_default_explicit_high_and_legacy_model_preserved(context, monkeypatch):  # noqa: F811
    from meeting_minutes.ai_policy import ensure_policy, policy
    settings, repo = context
    ensure_policy(repo,settings)
    assert policy(repo)['models']['claude_cli'] == 'claude-opus-5-5'
    runtime = runtime_for(context,monkeypatch)
    runtime = GenerationRuntime(settings,configuration(settings,{},'claude_cli','claude-opus-5-5'))
    def generate(argv, opts):
        assert argv[argv.index('--model')+1] == 'claude-opus-5-5'
        assert argv[argv.index('--effort')+1] == 'high'
        value = good()
        value['modelUsage'] = {'claude-opus-5-5':{}}
        return 0,json.dumps(value).encode(),b''
    monkeypatch.setattr('meeting_minutes.claude_provider.bounded_cli',fake_cli(generate))
    assert create_provider(runtime).generate({'segments':[]},SCHEMA,lambda:None)[1]['actual_model'] == 'claude-opus-5-5'

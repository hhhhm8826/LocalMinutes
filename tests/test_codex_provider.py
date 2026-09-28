import copy
import json
import os
import sys

import pytest

from meeting_minutes.codex_provider import CodexCliProvider, CodexFailure, DISABLED_FEATURES, bounded_cli, parse_events, strict_schema
from meeting_minutes.contracts import Minutes
from meeting_minutes.minutes_validation import text_payload, validate_minutes
from meeting_minutes.settings import Settings


def test_strict_schema_preserves_properties_named_title_and_default():
    from meeting_minutes.documents import GeneratedMeeting, GeneratedVideo
    for model, topic in [(GeneratedMeeting, 'GeneratedTopic'), (GeneratedVideo, 'GeneratedVideoTopic')]:
        schema = strict_schema(model.model_json_schema())
        properties = schema['$defs'][topic]['properties']
        assert 'title' in properties and 'title' in schema['$defs'][topic]['required']
        assert 'title' not in properties['title']
    schema = strict_schema({'type': 'object', 'title': 'annotation', 'properties': {
        'title': {'type': 'string', 'title': 'annotation'}, 'default': {'type': 'string', 'default': 'value'}}})
    assert set(schema['properties']) == {'title', 'default'}
    assert set(schema['required']) == {'title', 'default'}


def example():
    meeting = {'title': '가상 회의', 'language': 'ko', 'allow_external_text': True,
               'occurred_at': '2026-09-27T10:00:00+09:00', 'timezone': 'Asia/Seoul'}
    transcript = {'speakers': {'A': {'name': '화자 1'}}, 'embeddings': {'A': [999]}, 'secret': 'not outbound',
                  'segments': [{'id': 's1', 'text': '내일 검토하자. 담당자는 미정.', 'start_ms': 0, 'end_ms': 1000, 'speaker_id': 'A'}]}
    payload = text_payload('m1', 'v1', 1, meeting, transcript)
    value = {'meeting_id': 'm1', 'transcript_version': 'v1', 'revision': 1, 'summary': '검토를 논의했다.',
             'topics': [], 'decisions': [], 'open_questions': [], 'review_notes': [],
             'action_items': [{'id': 'a1', 'task': '검토', 'owner_speaker_id': None, 'due_date': '2026-09-28',
                               'due_date_original_expression': '내일', 'source_segment_ids': ['s1']}]}
    return meeting, payload, value


def test_text_boundary_and_semantic_validation():
    meeting, payload, value = example()
    assert 'embeddings' not in json.dumps(payload) and 'secret' not in json.dumps(payload)
    assert validate_minutes(value, payload, meeting).action_items[0].owner_speaker_id is None
    for field, bad in [('source_segment_ids', ['nonexistent']), ('owner_speaker_id', 'B'),
                       ('due_date', '2026-10-01'), ('review_status', 'verified')]:
        invalid = copy.deepcopy(value)
        invalid['action_items'][0][field] = bad
        with pytest.raises(CodexFailure):
            validate_minutes(invalid, payload, meeting)
    meeting['occurred_at'] = None
    with pytest.raises(CodexFailure, match='DATE_UNSUPPORTED'):
        validate_minutes(value, payload, meeting)
    value['action_items'][0]['due_date'] = None
    assert validate_minutes(value, payload, meeting)


def test_null_date_expression_requires_evidence_except_manual_authorship():
    meeting, payload, value = example()
    item = value['action_items'][0]
    item['due_date'] = None
    item['due_date_original_expression'] = '다음 주'
    for generated in (True, False):
        with pytest.raises(CodexFailure, match='MINUTES_DATE_UNSUPPORTED'):
            validate_minutes(value, payload, meeting, generated=generated)
    payload['segments'][0]['text'] = '다음 주 검토하자. 담당자는 미정.'
    assert validate_minutes(value, payload, meeting).action_items[0].due_date is None
    item['due_date_original_expression'] = None
    assert validate_minutes(value, payload, meeting)
    item.update(review_status='user_authored', source_segment_ids=[], due_date_original_expression='다음 달')
    assert validate_minutes(value, payload, meeting, generated=False)


def test_strict_schema_and_unexpected_tools():
    schema = strict_schema(Minutes.model_json_schema())
    assert schema['required'] == list(schema['properties'])
    assert 'default' not in json.dumps(schema)
    with pytest.raises(CodexFailure, match='UNEXPECTED_TOOL'):
        parse_events(b'{"type":"item.completed","item":{"type":"command_execution"}}')
    assert parse_events(b'{"type":"turn.failed","error":{"code":"usage_limit_reached"}}')['error_codes'] == ['CODEX_USAGE_LIMIT']


def test_bounded_cli_large_stdin_and_output_limit(tmp_path):
    env = {'PATH': '/usr/bin:/bin'}
    code, output, _ = bounded_cli([sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'],
        cwd=tmp_path, env=env, input_bytes=b'x' * 200000, timeout=3)
    assert code == 0 and int(output) == 200000
    with pytest.raises(CodexFailure, match='OUTPUT_LIMIT'):
        bounded_cli([sys.executable, '-c', 'print("x"*10000)'], cwd=tmp_path, env=env, timeout=3, output_limit=100)


def test_timeout_kills_cli_descendant(tmp_path):
    pid_file = tmp_path / 'child.pid'
    code = ('import subprocess,sys,time; from pathlib import Path; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
            'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
    with pytest.raises(CodexFailure, match='TIMEOUT'):
        bounded_cli([sys.executable, '-c', code, str(pid_file)], cwd=tmp_path, env=os.environ.copy(), timeout=.5)
    import psutil
    pid = int(pid_file.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_provider_keeps_secrets_and_source_out_of_argv_and_requires_complete_schema(tmp_path, monkeypatch):
    binary = tmp_path / 'codex'
    binary.write_text('test boundary')
    settings = Settings(codex_cli=binary, codex_home=tmp_path / 'auth', codex_user_home=tmp_path / 'home')
    settings.codex_home.mkdir()
    (settings.codex_home / 'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'gpt-6-astra', 'context_window': 272000, 'effective_context_window_percent': 95}]}))
    monkeypatch.setenv('OPENAI_API_KEY', 'private-canary')
    _, payload, value = example()
    result = Minutes.model_validate(value).model_dump()
    reservations = []
    def runner(argv, *, cwd, env, **kwargs):
        assert 'OPENAI_API_KEY' not in env and 'private-canary' not in json.dumps(env)
        assert payload['segments'][0]['text'] not in ' '.join(argv)
        if '--version' in argv:
            return 0, b'codex-cli 0.157.1\n', b''
        if 'login' in argv:
            return 0, b'Logged in using ChatGPT', b''
        if 'features' in argv:
            return 0, '\n'.join(f'{key} stable false' for key in DISABLED_FEATURES).encode(), b''
        assert '--ignore-user-config' in argv and '--ephemeral' in argv and '--ignore-rules' in argv
        assert payload['segments'][0]['text'] in kwargs['input_bytes'].decode()
        assert reservations == ['reserved']
        kwargs['result_path'].write_text(json.dumps(result))
        return 0, b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":20}}', b''
    monkeypatch.setattr('meeting_minutes.codex_provider.bounded_cli', runner)
    provider = CodexCliProvider(settings)
    received, metrics = provider.generate(payload, Minutes.model_json_schema(), lambda: reservations.append('reserved'))
    assert received['summary'] == result['summary'] and metrics['completed']
    result.pop('language')
    reservations.clear()
    with pytest.raises(CodexFailure, match='SCHEMA_INVALID'):
        provider.generate(payload, Minutes.model_json_schema(), lambda: reservations.append('reserved'))


def test_cli_state_growth_does_not_relax_result_limit(tmp_path):
    state = tmp_path / 'state.sqlite-wal'
    state.write_bytes(b'x' * 2_000_000)
    code, output, _ = bounded_cli(
        [sys.executable, '-c', "with open('state.sqlite-wal','ab') as f: f.write(b'x'*1_000_000)"],
        cwd=tmp_path, env=os.environ.copy(), timeout=5)
    assert code == 0 and state.stat().st_size == 3_000_000
    result = tmp_path / 'result.json'
    with pytest.raises(CodexFailure, match='CODEX_OUTPUT_LIMIT'):
        bounded_cli([sys.executable, '-c',
                     "from pathlib import Path; import time; Path('result.json').write_bytes(b'x'*1_000_001); time.sleep(2)"],
                    cwd=tmp_path, env=os.environ.copy(), timeout=5, result_path=result)


@pytest.mark.parametrize('tokens, rejected', [(64, False), (65, False), (None, True), (True, True), (-1, True)])
def test_output_usage_required_but_over_reserve_result_kept(tmp_path, monkeypatch, tokens, rejected):
    from types import SimpleNamespace
    settings = Settings(codex_home=tmp_path / 'auth', codex_user_home=tmp_path / 'home')
    provider = CodexCliProvider(settings)
    provider.runtime = SimpleNamespace(budget=lambda schema: {
        'max_prompt_bytes': 10000, 'output_reserve_tokens': 64})
    monkeypatch.setattr(provider, 'preflight', lambda *args: None)
    def run(argv, **kwargs):
        kwargs['result_path'].write_text('{"summary":"small"}')
        return 0, json.dumps({'type':'turn.completed', 'usage':{'output_tokens':tokens}}).encode(), b''
    monkeypatch.setattr('meeting_minutes.codex_provider.bounded_cli', run)
    schema = {'type':'object','properties':{'summary':{'type':'string'}},'required':['summary']}
    if rejected:
        with pytest.raises(CodexFailure, match='CODEX_OUTPUT_LIMIT|CODEX_PROTOCOL_ERROR'):
            provider.generate({'segments':[]}, schema, lambda: None)
    else:
        assert provider.generate({'segments':[]}, schema, lambda: None)[0]['summary'] == 'small'

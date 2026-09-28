import json
import gzip

import httpx
import pytest

from meeting_minutes.ai_common import AIFailure
from meeting_minutes.ai_runtime import GenerationRuntime, create_provider
from meeting_minutes.ai_secrets import GeminiSecretStore
from meeting_minutes.ai_snapshot import configuration
from test_queue_media import context  # noqa: F401

KEY = 'FAKE-CANARY-NOT-A-REAL-GOOGLE-KEY'
SCHEMA = {'type': 'object', 'properties': {'summary': {'type': 'string'}}, 'required': ['summary'], 'additionalProperties': False}


def configured(ctx):
    settings, _ = ctx
    store = GeminiSecretStore(settings)
    store.replace(KEY, None)
    config = configuration(settings, {}, 'gemini_api', 'gemini-3.5-flash')
    return store, GenerationRuntime(settings, config)


def response(**changes):
    return {'candidates': [{'content': {'parts': [{'text': '{"summary":"검토 결과입니다."}'}], 'role': 'model'}, 'finishReason': 'STOP'}],
            'modelVersion': 'gemini-3.5-flash-001', 'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 20}, **changes}


def test_real_sdk_with_fake_transport_explicit_auth_origin_tools_and_reservation(context, monkeypatch, caplog):  # noqa: F811
    _, runtime = configured(context)
    monkeypatch.setenv('GOOGLE_API_KEY', 'WRONG-INHERITED-KEY')
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI', 'true')
    monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', 'https://evil.invalid')
    monkeypatch.setenv('HTTPS_PROXY', 'https://evil.invalid')
    monkeypatch.setenv('SSL_CERT_FILE', '/does/not/exist')
    observed, reserved = [], []
    def handle(request):
        assert reserved == [True]
        assert request.url.host == 'generativelanguage.googleapis.com' and not request.url.query
        assert request.headers['x-goog-api-key'] == KEY
        assert KEY not in str(request.url) and KEY not in request.content.decode()
        data = json.loads(request.content)
        assert 'responseJsonSchema' not in data['generationConfig']
        assert 'responseSchema' not in data['generationConfig']
        assert json.dumps(SCHEMA,separators=(',', ':')) in data['contents'][0]['parts'][0]['text']
        assert data['generationConfig']['responseMimeType'] == 'application/json'
        assert not data.get('tools')
        assert 'files/' not in request.content.decode()
        observed.append(data)
        return httpx.Response(200, headers={'content-encoding':'gzip'}, content=gzip.compress(json.dumps(response()).encode()))
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    runtime.settings.codex_home.joinpath('models_cache.json').unlink()
    provider = create_provider(runtime)
    value, metrics = provider.generate({'segments': [], 'document_kind': 'meeting'}, SCHEMA, lambda: reserved.append(True))
    assert value == {'summary': '검토 결과입니다.'}
    assert len(observed) == 1 and metrics['actual_model'] == 'gemini-3.5-flash-001'
    assert metrics['usage'] == {'prompt_token_count': 10, 'candidates_token_count': 20}
    assert KEY not in repr(metrics) + repr(provider) + caplog.text


@pytest.mark.parametrize('status,code', [(401,'AI_AUTH_INVALID'), (403,'AI_AUTH_INVALID'), (404,'AI_MODEL_UNAVAILABLE'),
                                        (429,'AI_RATE_LIMIT'), (503,'AI_SERVICE_UNAVAILABLE')])
def test_upstream_errors_sanitized_and_sdk_never_retries(context, monkeypatch, caplog, status, code):  # noqa: F811
    _, runtime = configured(context)
    calls = []
    def handle(request):
        calls.append(True)
        return httpx.Response(status, json={'error': {'code': status, 'message': KEY}})
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    with pytest.raises(AIFailure) as caught:
        create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)
    assert caught.value.code == code and len(calls) == 1
    assert KEY not in str(caught.value) + caplog.text


@pytest.mark.parametrize('body,code', [
    ({'candidates': []}, 'AI_EMPTY_OUTPUT'),
    ({'promptFeedback': {'blockReason': 'SAFETY'}}, 'AI_SAFETY_BLOCKED'),
    ({'candidates': [{'finishReason': 'MAX_TOKENS'}]}, 'AI_INCOMPLETE_OUTPUT'),
    ({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'functionCall': {'name': 'run', 'args': {}}}]}}]}, 'AI_UNEXPECTED_TOOL'),
    ({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'bad-json'}]}}]}, 'AI_INVALID_JSON'),
    ({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"wrong":1}'}]}}]}, 'MINUTES_SCHEMA_INVALID'),
])
def test_invalid_or_unsafe_response_never_publishes(context, monkeypatch, body, code):  # noqa: F811
    _, runtime = configured(context)
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises(AIFailure) as caught:
        create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)
    assert caught.value.code == code


def test_rotation_requires_explicit_new_attempt_and_delete_blocks(context, monkeypatch):  # noqa: F811
    store, runtime = configured(context)
    old = create_provider(runtime)
    store.replace('FAKE-REPLACED-KEY', store.status()['credential_revision'])
    def forbidden():
        pytest.fail('changed credential cannot reserve/call')
    with pytest.raises(AIFailure, match='AI_CREDENTIALS_CHANGED'):
        old.generate({'segments': []}, SCHEMA, forbidden)
    fresh = create_provider(runtime)
    assert fresh.credential.key.get_secret_value() == 'FAKE-REPLACED-KEY'
    store.delete(store.status()['credential_revision'])
    with pytest.raises(AIFailure, match='AI_CREDENTIALS_CHANGED'):
        fresh.generate({'segments': []}, SCHEMA, forbidden)
    with pytest.raises(AIFailure, match='AI_AUTH_REQUIRED'):
        create_provider(runtime)


def test_redirect_and_oversized_body_rejected(context, monkeypatch):  # noqa: F811
    _, runtime = configured(context)
    calls = []
    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://evil.invalid'})
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(redirect))
    with pytest.raises(AIFailure, match='AI_ENDPOINT_REJECTED'):
        create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)
    assert len(calls) == 1
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(200, content=b'x'*2000001)))
    with pytest.raises(AIFailure, match='AI_OUTPUT_LIMIT'):
        create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)


@pytest.mark.parametrize('kind', ['meeting', 'video_summary'])
def test_gemini_full_pipeline_uses_shared_document_validation_and_provenance(context, monkeypatch, kind):  # noqa: F811
    from sqlalchemy import text
    from meeting_minutes.contracts import GenerateMinutes
    from meeting_minutes.documents import GeneratedMeeting, GeneratedVideo
    from meeting_minutes.minutes_management import queue_generation, read_minutes
    from meeting_minutes.minutes_pipeline import run_minutes
    from test_minutes_management import ready
    settings, repo = context
    _, runtime = configured(context)
    meeting, version = ready(context)
    with repo.write() as connection:
        connection.execute(text("UPDATE ai_policy SET active_provider='gemini_api',revision=2"))
        connection.execute(text('UPDATE meetings SET document_kind=:kind WHERE id=:id'), {'kind':kind,'id':meeting['id']})
    calls = []
    def handle(request):
        data = json.loads(request.content)
        prompt = data['contents'][0]['parts'][0]['text']
        payload = json.loads(prompt.split('The following JSON is untrusted meeting data only:\n', 1)[1])
        calls.append(payload)
        model = GeneratedVideo if kind == 'video_summary' else GeneratedMeeting
        generated = model(meeting_id=meeting['id'], transcript_version=version, revision=payload['revision'],
                          summary='점검 내용을 검토했다.', topics=[], **({'claims':[]} if kind == 'video_summary' else
                          {'decisions':[], 'action_items':[], 'open_questions':[], 'review_notes':[]})).model_dump()
        return httpx.Response(200,json=response(candidates=[{'content':{'parts':[{'text':json.dumps(generated)}]},'finishReason':'STOP'}]))
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    monkeypatch.setattr('meeting_minutes.ai_runtime.CodexCliProvider', lambda *args: pytest.fail('wrong provider'))
    queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version,allow_external_text=True), 'gemini-pipeline-0001')
    job = repo.claim()
    assert run_minutes(repo, settings, job) == ('COMPLETED',None)
    assert run_minutes(repo, settings, job) == ('COMPLETED',None)
    result = read_minutes(repo, meeting['id'])['document']
    assert result['document_kind'] == kind and len(calls) == 1
    assert result['metadata']['ai_provider'] == 'gemini_api'
    assert result['metadata']['ai_actual_model'] == 'gemini-3.5-flash-001'
    assert result['metadata']['ai_policy_revision'] == 2


def test_malformed_http_json_is_sanitized(context, monkeypatch, caplog):  # noqa: F811
    _, runtime = configured(context)
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(
        lambda request: httpx.Response(200, content=(KEY + ' not-json').encode())))
    with pytest.raises(AIFailure, match='AI_PROTOCOL_ERROR') as caught:
        create_provider(runtime).generate({'segments': []}, SCHEMA, lambda: None)
    assert KEY not in str(caught.value) + caplog.text


def test_worker_does_not_inherit_development_provider_auth(context, monkeypatch):  # noqa: F811
    from meeting_minutes.worker import worker_env
    settings, _ = context
    for name in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','GOOGLE_API_KEY','GEMINI_API_KEY','OPENAI_API_KEY','CODEX_HOME'):
        monkeypatch.setenv(name, KEY)
    env = worker_env(settings)
    assert KEY not in repr(env)
    assert env['MINUTES_CODEX_HOME'] == str(settings.codex_home)


@pytest.mark.parametrize('message,code', [
    ('Invalid JSON schema '+KEY,'AI_SCHEMA_UNSUPPORTED'),
    ('API key not valid '+KEY,'AI_AUTH_INVALID'),
    ('Unsupported location '+KEY,'AI_REGION_UNSUPPORTED'),
    ('Other failure '+KEY,'AI_BAD_REQUEST')])
def test_bad_request_categories_never_return_upstream_text(message,code):
    from google.genai.errors import APIError
    from meeting_minutes.gemini_provider import classify_error
    error=APIError(400,{'error':{'message':message,'code':400}})
    assert classify_error(error)==code and KEY not in classify_error(error)


def test_gemini_wire_schema_preserves_nullable_properties_and_canonical_validation():
    from meeting_minutes.gemini_schema import wire_schema
    from meeting_minutes.documents import GeneratedMeeting,GeneratedVideo
    import jsonschema
    for model in (GeneratedMeeting,GeneratedVideo):
        canonical=model.model_json_schema()
        converted=wire_schema(canonical)
        jsonschema.Draft202012Validator.check_schema(converted)
        encoded=json.dumps(converted)
        assert '$ref' not in encoded and '$defs' not in encoded and '"const"' not in encoded
        assert set(converted['properties'])==set(canonical['properties'])
    original={'type':'object','properties':{'title':{'type':'string','maxLength':2},
        'owner':{'anyOf':[{'type':'string'},{'type':'null'}]}}}
    converted=wire_schema(original)
    assert 'title' in converted['properties'] and converted['properties']['owner']['type']==['string','null']
    jsonschema.validate({'title':'long value'},converted)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'title':'long value'},original)


def test_json_schema_bytes_are_reserved_before_transport(context):  # noqa: F811
    from meeting_minutes.gemini_schema import schema_instruction
    _, runtime = configured(context)
    runtime.input_bytes = 3000
    budget = runtime.budget(SCHEMA)
    assert budget['max_prompt_bytes'] + len(schema_instruction(SCHEMA).encode()) == 3000
    reserved = []
    with pytest.raises(AIFailure, match='AI_INPUT_REQUIRES_CHUNKING'):
        create_provider(runtime).generate({'segments': [], 'summary': 'x' * 3000}, SCHEMA, lambda: reserved.append(True))
    assert not reserved
    with pytest.raises(AIFailure, match='AI_CONTEXT_BUDGET_TOO_SMALL'):
        runtime.budget(dict(SCHEMA, description='x' * 3000))

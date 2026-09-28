import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.ai_policy import readiness
from test_gemini_provider import KEY
from test_queue_media import context  # noqa: F401


def test_owner_check_is_bounded_cached_and_never_generates(context, monkeypatch):  # noqa: F811
    settings, repo = context
    calls = []
    def handle(request):
        assert request.method == 'GET' and request.url.path == '/v1beta/models/gemini-3.5-flash'
        calls.append(True)
        return httpx.Response(200, json={'name':'models/gemini-3.5-flash', 'supportedGenerationMethods':['generateContent']})
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    # Avoid unrelated local CLI probes in this HTTP fixture.
    monkeypatch.setattr('meeting_minutes.diagnostics.runtime_status', lambda settings: {'error':None,'login':'chatgpt',
        'configured_model_in_local_catalog':True,'checked_at':1})
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        headers = {'origin': settings.origin}
        assert client.post('/api/settings/ai/gemini_api/check', json={'model':'gemini-3.5-flash'},headers=headers).status_code == 401
        headers['x-csrf-token'] = client.post('/api/auth/login',json={'key':settings.owner_key_path.read_text()},headers=headers).json()['csrf_token']
        assert client.put('/api/settings/ai/gemini-key',json={'expected_credential_revision':None,'key':KEY},headers=headers).status_code == 200
        for _ in range(2):
            result = client.post('/api/settings/ai/gemini_api/check',json={'model':'gemini-3.5-flash'},headers=headers)
            assert result.status_code == 200 and result.json()['ready'] is True
            assert result.headers['cache-control'] == 'no-store' and KEY not in result.text
        assert len(calls) == 1
        selected = client.patch('/api/settings/ai',json={'expected_revision':1,'active_provider':'gemini_api','model':'gemini-3.5-flash'},headers=headers)
        assert selected.status_code == 200
        for _ in range(3):
            assert client.get('/api/settings/ai').status_code == 200
            assert client.get('/api/ai-status').json() == {'label':'Gemini API','ready':True}
        assert len(calls) == 1
        key_revision = client.get('/api/settings/ai').json()['gemini_key']['credential_revision']
        assert client.delete('/api/settings/ai/gemini-key',headers=headers).status_code == 422
        result = client.request('DELETE','/api/settings/ai/gemini-key',json={'expected_credential_revision':key_revision},headers=headers)
        assert result.status_code == 200
        assert client.get('/api/ai-status').json() == {'label':'Gemini API','ready':False}
        assert client.get('/api/settings/ai').json()['active_provider'] == 'gemini_api'
    assert len(calls) == 1
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM usage_records')).scalar_one() == 0


def test_key_change_during_model_check_discards_readiness(context, monkeypatch):  # noqa: F811
    from meeting_minutes.ai_checks import check_provider
    from test_gemini_provider import configured
    store, runtime = configured(context)
    settings, repo = context
    def handle(request):
        store.replace('FAKE-ROTATED', store.status()['credential_revision'])
        return httpx.Response(200,json={'name':'models/gemini-3.5-flash','supportedGenerationMethods':['generateContent']})
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    result = check_provider(repo, settings, 'gemini_api', runtime.model)
    assert result['ready'] is False and result['code'] == 'AI_CREDENTIALS_CHANGED'
    assert readiness(repo, settings, 'gemini_api', runtime.model)['ready'] is False


def test_codex_catalog_presence_without_limits_is_not_selectable(context, monkeypatch):  # noqa: F811
    import json
    settings,repo=context
    (settings.codex_home/'models_cache.json').write_text(json.dumps({'models':[{'slug':settings.codex_model}]}))
    monkeypatch.setattr('meeting_minutes.diagnostics.runtime_status',lambda settings:{'error':None,'login':'chatgpt',
        'configured_model_in_local_catalog':True,'checked_at':1})
    result=readiness(repo,settings,'codex_cli',settings.codex_model)
    assert result['ready'] is False and result['code']=='AI_MODEL_METADATA_REQUIRED'


def test_claude_expired_cache_cannot_be_committed_after_initial_readiness(context, monkeypatch):  # noqa: F811
    import time
    import pytest
    from meeting_minutes import ai_policy
    from meeting_minutes.repository import Conflict
    settings,repo=context
    ai_policy.ensure_policy(repo,settings)
    with repo.write() as connection:
        connection.execute(text("INSERT INTO ai_readiness VALUES ('claude_cli','claude-sonnet-4-6',NULL,'ready',NULL,:when)"),{'when':time.time()-301})
    monkeypatch.setattr(ai_policy,'readiness',lambda *args:{'ready':True,'code':None})
    with pytest.raises(Conflict,match='AI_CONNECTION_CHECK_REQUIRED'):
        ai_policy.update_policy(repo,settings,ai_policy.AIUpdate(expected_revision=1,active_provider='claude_cli',model='claude-sonnet-4-6'))
    assert ai_policy.policy(repo)['active_provider']=='codex_cli'

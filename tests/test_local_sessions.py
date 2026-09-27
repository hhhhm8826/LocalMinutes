import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from test_queue_media import context  # noqa: F401


def test_keyless_session_permissions_csrf_logout_and_other_tab(context):  # noqa: F811
    settings, repo = context
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert client.post('/api/auth/local').status_code == 403
        assert client.post('/api/auth/local', headers={'origin': 'https://evil.test'}).status_code == 403
        assert client.post('/api/auth/local', headers={'origin': settings.origin, 'sec-fetch-site': 'cross-site'}).status_code == 403
        response = client.post('/api/auth/local', headers={'origin': settings.origin})
        assert response.status_code == 200 and response.json()['owner'] is False
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        headers = {'origin': settings.origin, 'x-csrf-token': response.json()['csrf_token'], 'idempotency-key': 'keyless-create-0001'}
        assert client.get('/api/meetings').status_code == 200
        assert client.get('/api/settings/retention').status_code == 401
        assert client.get('/api/diagnostics').status_code == 401
        assert client.post('/api/meetings', json={'title': '회의'}, headers={'origin': settings.origin}).status_code == 403
        body = {'title': '회의', 'language': 'en', 'speakers': 4, 'allow_external_text': False}
        created = client.post('/api/meetings', json=body, headers=headers)
        assert created.status_code == 200
        options = json.loads(created.json()['settings_json'])
        assert options['language'] == 'auto' and options['speakers'] is None and options['allow_external_text']
        assert client.post('/api/meetings', json=body, headers=headers).json()['id'] == created.json()['id']
        assert client.post('/api/meetings', json={'title': '다른 회의'}, headers=headers).status_code == 409
        assert len(repo.meetings()) == 1
        logged_in = client.post('/api/auth/login', json={'key': settings.owner_key_path.read_text()}, headers={'origin': settings.origin})
        assert logged_in.json()['csrf_token'] == headers['x-csrf-token']
        assert logged_in.json()['owner']
        assert client.get('/api/settings/retention').status_code == 200
        old_cookies = dict(client.cookies)
        assert client.post('/api/auth/logout', headers=headers).status_code == 200
        assert client.get('/api/meetings').status_code == 200
        assert client.get('/api/settings/retention').status_code == 401
        client.cookies.update(old_cookies)
        assert not client.get('/api/auth/session').json()['owner']
        assert client.get('/api/settings/retention').status_code == 401
        assert client.get('/api/meetings').status_code == 200
        client.post('/api/auth/login', json={'key': settings.owner_key_path.read_text()}, headers={'origin': settings.origin})
        with repo.write() as connection:
            connection.execute(text('UPDATE owner_sessions SET expires_at=0'))
        assert not client.get('/api/auth/session').json()['owner']
        assert client.get('/api/diagnostics').status_code == 401
        assert client.get('/api/meetings').status_code == 200

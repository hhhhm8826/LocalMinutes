import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes import ai_policy
from meeting_minutes.ai_secrets import GeminiSecretStore
from meeting_minutes.operations import backup, restore
from test_foundation import settings  # noqa: F401


def login(client, settings):  # noqa: F811
    response = client.post('/api/auth/login', headers={'origin':settings.origin}, json={'key':settings.owner_key_path.read_text()})
    return {'origin':settings.origin, 'x-csrf-token':response.json()['csrf_token']}


def test_key_api_owner_csrf_errors_never_echo_and_restart(settings):  # noqa: F811
    canary = 'private-canary-value'
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert client.get('/api/settings/ai').status_code == 401
        local = client.post('/api/auth/local', headers={'origin':settings.origin}).json()
        body = {'key':canary, 'expected_credential_revision':None}
        headers = {'origin':settings.origin, 'x-csrf-token':local['csrf_token']}
        assert client.put('/api/settings/ai/gemini-key', json=body, headers=headers).status_code == 401
        headers = login(client, settings)
        assert client.put('/api/settings/ai/gemini-key', json=body, headers={'origin':settings.origin}).status_code == 403
        saved = client.put('/api/settings/ai/gemini-key', json=body, headers=headers)
        assert saved.status_code == 200 and canary not in saved.text
        assert saved.headers['cache-control'] == 'no-store'
        revision = saved.json()['credential_revision']
        conflict = client.put('/api/settings/ai/gemini-key', json=body, headers=headers)
        assert conflict.status_code == 409 and canary not in conflict.text
        for bad in [dict(body, key={'raw':canary}), dict(body, extra=canary), dict(body, key=canary+'\n')]:
            response = client.put('/api/settings/ai/gemini-key', json=bad, headers=headers)
            assert response.status_code == 422 and canary not in response.text
        response = client.put('/api/settings/ai/gemini-key', content='{"key":"'+canary, headers=headers)
        assert response.status_code == 422 and canary not in response.text
        status = client.get('/api/settings/ai')
        assert status.status_code == 200 and canary not in status.text
        assert status.json()['active_provider'] == 'codex_cli'
        assert status.json()['gemini_key']['registered']
        denied = client.patch('/api/settings/ai', json={'active_provider':'gemini_api','model':'gemini-3.5-flash','expected_revision':1},headers=headers)
        assert denied.status_code == 409
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        headers = login(client, settings)
        assert client.get('/api/settings/ai').json()['gemini_key']['credential_revision'] == revision
        result = client.request('DELETE','/api/settings/ai/gemini-key',json={'expected_credential_revision':revision},headers=headers)
        assert result.status_code == 200 and not result.json()['registered']
        assert client.get('/api/settings/ai').json()['active_provider'] == 'codex_cli'
    assert canary.encode() not in settings.database_path.read_bytes()
    assert not any(canary.encode() in p.read_bytes() for p in (settings.data_dir/'logs').glob('*') if p.is_file())


def test_policy_preserves_initial_model_and_conflicts(settings, monkeypatch):  # noqa: F811
    custom = settings.model_copy(update={'codex_model':'existing-custom-model'})
    with TestClient(create_app(custom), base_url=settings.origin) as client:
        headers = login(client, settings)
        assert client.get('/api/settings/ai').json()['models']['codex_cli'] == 'existing-custom-model'
        monkeypatch.setattr(ai_policy,'readiness',lambda *args: {'ready':True,'code':None,'checked_at':0})
        import time
        with client.app.state.repository.write() as connection:
            connection.execute(text("INSERT INTO ai_readiness VALUES ('claude_cli','claude-sonnet-4-6',NULL,'ready',NULL,:now)"), {'now':time.time()})
        body={'active_provider':'claude_cli','model':'claude-sonnet-4-6','expected_revision':1}
        assert client.patch('/api/settings/ai',json=body,headers=headers).status_code == 200
        assert client.patch('/api/settings/ai',json=body,headers=headers).status_code == 409
    with TestClient(create_app(settings),base_url=settings.origin) as client:
        login(client,settings)
        state=client.get('/api/settings/ai').json()
        assert state['active_provider']=='claude_cli' and state['revision']==2
        assert state['models']['codex_cli']=='existing-custom-model'


def test_backup_restores_policy_without_readiness_or_credentials(settings, tmp_path):  # noqa: F811
    with TestClient(create_app(settings),base_url=settings.origin) as client:
        login(client,settings)
        repo=client.app.state.repository
        with repo.write() as connection:
            connection.execute(text("INSERT INTO ai_readiness VALUES ('gemini_api','model','random','ready',NULL,1)"))
        GeminiSecretStore(settings).replace('private-canary',None)
    archive=tmp_path/'backup.tar.gz'
    backup(settings,archive)
    destination=tmp_path/'restored'
    restore(archive,destination)
    with sqlite3.connect(destination/'minutes.sqlite3') as connection:
        assert connection.execute('SELECT COUNT(*) FROM ai_policy').fetchone()[0]==1
        assert connection.execute('SELECT COUNT(*) FROM ai_readiness').fetchone()[0]==0
    assert not list(destination.rglob('.apikey'))
    (settings.data_dir/'artifacts/auth.json').write_text('private-canary')
    with pytest.raises(RuntimeError):
        backup(settings,tmp_path/'rejected.tar.gz')
    assert not (tmp_path/'rejected.tar.gz').exists()


def test_sync_rejects_tracked_secrets_before_copying(tmp_path):
    spec=importlib.util.spec_from_file_location('source_sync',Path(__file__).parents[1]/'scripts/sync-source.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source=tmp_path/'source'
    source.mkdir()
    subprocess.run(['git','init','-q',str(source)],check=True)
    (source/'public.txt').write_text('public')
    (source/'.apikey').mkdir()
    (source/'.apikey/gemini.json').write_text(json.dumps({'key':'private-canary'}))
    subprocess.run(['git','add','.'],cwd=source,check=True)
    with pytest.raises(ValueError,match='PRIVATE_SOURCE_PATH'):
        module.sync(source,tmp_path/'destination')
    assert not (tmp_path/'destination').exists()


def test_release_collector_rejects_misplaced_secret(tmp_path):
    spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/build-release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Build a minimal filesystem matching the collector's public allowlist.
    names = set(module.REQUIRED) | {'scripts/' + n for n in module.SCRIPTS}
    names.update('apps/web/' + n for n in ('index.html', 'playwright.config.ts', 'tsconfig.json', 'vite.config.ts'))
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    secret = tmp_path / 'src/nested/.apikey/gemini.json'
    secret.parent.mkdir(parents=True)
    secret.write_text('private-canary')
    with pytest.raises(ValueError, match='Private release'):
        module.collect(tmp_path)


def test_app_rejects_config_under_static_public_root(settings):  # noqa: F811
    from meeting_minutes.ai_secrets import SecretStoreError
    public = settings.data_dir / 'web'
    unsafe = settings.model_copy(update={'web_dir':public, 'config_dir':public / 'config'})
    with pytest.raises(SecretStoreError, match='AI_SECRET_PATH_UNSAFE'):
        with TestClient(create_app(unsafe)):
            pass
    assert not unsafe.owner_key_path.exists()


def test_owner_import_key_path_is_private_at_any_depth():
    from meeting_minutes.private_paths import private_path
    assert private_path('.gemini_api_key')
    assert private_path('nested/.gemini_api_key')
    assert private_path(r'nested\.gemini_api_key')


def test_standalone_claude_state_is_private():
    from meeting_minutes.private_paths import private_path
    for name in ('.claude.json', '.claude.json.backup', '.claude.json.backup.123', 'nested/.claude.json', r'nested\.claude.json.backup'):
        assert private_path(name)
    assert not private_path('src/meeting_minutes/claude_provider.py')


@pytest.mark.parametrize('old_model', ['gemini-3.5-flash', 'custom-explicit-model'])
def test_gemini_migration_preserves_selection_and_custom_model(tmp_path, old_model):
    from alembic import command
    from alembic.config import Config
    from meeting_minutes.storage import make_engine, migrate
    engine = make_engine(tmp_path / 'migration.sqlite3')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/meeting_minutes/migrations'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0014')
        connection.execute(text("INSERT INTO ai_policy (id,active_provider,models_json,revision,updated_at) VALUES (1,'claude_cli',:models,7,1)"), {'models':json.dumps({'gemini_api':old_model,'claude_cli':'custom-claude'})})
        connection.execute(text("INSERT INTO ai_readiness VALUES ('gemini_api',:model,NULL,'ready',NULL,1)"), {'model':old_model})
    migrate(engine)
    with engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM ai_policy')).mappings().one()
        assert row['active_provider'] == 'claude_cli'
        assert json.loads(row['models_json']) == {'gemini_api':'gemini-3.8-flash' if old_model == 'gemini-3.5-flash' else old_model, 'claude_cli':'custom-claude'}
        assert row['revision'] == (8 if old_model == 'gemini-3.5-flash' else 7)
        assert connection.execute(text('SELECT COUNT(*) FROM ai_readiness')).scalar_one() == 0


@pytest.mark.parametrize('old_model', ['claude-sonnet-4-6', 'custom-claude'])
def test_opus_default_migration_preserves_active_provider(tmp_path, old_model):
    from alembic import command
    from alembic.config import Config
    from meeting_minutes.storage import make_engine, migrate
    engine = make_engine(tmp_path / 'opus.sqlite3')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/meeting_minutes/migrations'))
    with engine.begin() as c:
        config.attributes['connection'] = c
        command.upgrade(config,'0016')
        c.execute(text("INSERT INTO ai_policy VALUES (1,'codex_cli',:models,9,1)"), {'models':json.dumps({'claude_cli':old_model,'codex_cli':'gpt-6-astra'})})
        c.execute(text("INSERT INTO ai_readiness VALUES ('claude_cli',:model,NULL,'ready',NULL,1)"), {'model':old_model})
    migrate(engine)
    with engine.connect() as c:
        row = c.execute(text('SELECT * FROM ai_policy')).mappings().one()
        assert row['active_provider'] == 'codex_cli'
        assert json.loads(row['models_json'])['claude_cli'] == ('claude-opus-5-5' if old_model == 'claude-sonnet-4-6' else old_model)
        assert row['revision'] == (10 if old_model == 'claude-sonnet-4-6' else 9)
        assert c.execute(text('SELECT COUNT(*) FROM ai_readiness')).scalar_one() == 0

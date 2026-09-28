"""비밀 없는 전역 AI 정책. 소유자 변경은 별도 revision으로 충돌을 검사합니다."""
import json
from contextlib import nullcontext
import time
from typing import Literal

from pydantic import ConfigDict, Field, SecretStr
from sqlalchemy import text

from .contracts import Contract
from .repository import Conflict
from .ai_secrets import GeminiSecretStore, SecretStoreError

ProviderId = Literal['codex_cli', 'gemini_api', 'claude_cli']
PROVIDERS = {'codex_cli': 'Codex CLI', 'gemini_api': 'Gemini API', 'claude_cli': 'Claude CLI'}


class AIUpdate(Contract):
    expected_revision: int = Field(ge=1)
    active_provider: ProviderId
    model: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._-]+$')


class AICheck(Contract):
    model: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._-]+$')

class KeyUpdate(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    expected_credential_revision: str | None = Field(max_length=32)
    key: SecretStr = Field(min_length=1, max_length=4096)


class KeyDelete(Contract):
    expected_credential_revision: str | None = Field(max_length=32)


def ensure_policy(repository, settings):
    # First initialization captures the existing installation's Codex model.
    models = {'codex_cli': settings.codex_model, 'gemini_api': 'gemini-3.8-flash', 'claude_cli': 'claude-opus-5-5'}
    with repository.write() as connection:
        connection.execute(text('INSERT OR IGNORE INTO ai_policy VALUES (1, :provider, :models, 1, :now)'),
            {'provider': 'codex_cli', 'models': json.dumps(models), 'now': time.time()})


def policy(repository):
    with repository.engine.connect() as connection:
        row = dict(connection.execute(text('SELECT * FROM ai_policy WHERE id=1')).mappings().one())
    row.pop('id')
    row['models'] = json.loads(row.pop('models_json'))
    return row


def readiness(repository, settings, provider, model):
    # No generation or model network requests during polling.
    if provider == 'codex_cli':
        from .diagnostics import runtime_status
        status = runtime_status(settings.model_copy(update={'codex_model': model}))
        code = status['error']
        if status['login'] != 'chatgpt':
            code = code or 'AI_AUTH_REQUIRED'
        if not status['configured_model_in_local_catalog']:
            code = code or 'AI_MODEL_UNAVAILABLE'
        if code is None:
            from .ai_capabilities import discover
            if discover(settings, provider, model) is None:
                code = 'AI_MODEL_METADATA_REQUIRED'
        return {'ready': code is None, 'code': code, 'checked_at': status['checked_at']}
    if provider == 'gemini_api':
        try:
            credential = GeminiSecretStore(settings).status()
        except SecretStoreError as exc:
            return {'ready': False, 'code': exc.code, 'checked_at': None}
        if not credential['registered']:
            return {'ready': False, 'code': 'AI_AUTH_REQUIRED', 'checked_at': None}
        with repository.engine.connect() as connection:
            row = connection.execute(text('SELECT * FROM ai_readiness WHERE provider=:provider'), {'provider':provider}).mappings().first()
        if (row and row['model'] == model and row['credential_revision'] == credential['credential_revision']
                and row['checked_at'] > time.time() - 300):
            return {'ready': row['status'] == 'ready', 'code': row['code'], 'checked_at': row['checked_at']}
        return {'ready': False, 'code': 'AI_CONNECTION_CHECK_REQUIRED', 'checked_at': None}
    if not settings.claude_cli.is_file():
        return {'ready': False, 'code': 'AI_INSTALL_REQUIRED', 'checked_at': None}
    if not settings.claude_home.is_dir() or not settings.claude_user_home.is_dir():
        return {'ready': False, 'code': 'AI_AUTH_REQUIRED', 'checked_at': None}
    # An explicit check populates this cache; GET never launches Claude.
    with repository.engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM ai_readiness WHERE provider='claude_cli'")).mappings().first()
    if row and row['model']==model and row['checked_at']>time.time()-300:
        return {'ready':row['status']=='ready','code':row['code'],'checked_at':row['checked_at']}
    return {'ready':False,'code':'AI_CONNECTION_CHECK_REQUIRED','checked_at':None}


def owner_status(repository, settings):
    value = policy(repository)
    value['providers'] = [{'id': provider, 'label': label, 'model': value['models'][provider],
        **readiness(repository, settings, provider, value['models'][provider])} for provider, label in PROVIDERS.items()]
    try:
        value['gemini_key'] = GeminiSecretStore(settings).status()
    except SecretStoreError as exc:
        value['gemini_key'] = {'registered': False, 'credential_revision': None, 'updated_at': None, 'code': exc.code}
    return value


def update_policy(repository, settings, request):
    available = readiness(repository, settings, request.active_provider, request.model)
    if not available['ready']:
        raise Conflict(available['code'] or 'AI_NOT_READY')
    store = GeminiSecretStore(settings) if request.active_provider == 'gemini_api' else None
    with store.locked() if store else nullcontext() as directory, repository.write() as connection:
        if store:
            credential = store._read(directory)
            cache = connection.execute(text("SELECT * FROM ai_readiness WHERE provider='gemini_api'")).mappings().first()
            if (credential is None or cache is None or cache['status'] != 'ready'
                    or cache['credential_revision'] != credential.revision or cache['model'] != request.model
                    or cache['checked_at'] <= time.time() - 300):
                raise Conflict('AI_CONNECTION_CHECK_REQUIRED')
        if request.active_provider == 'claude_cli':
            cache = connection.execute(text("SELECT * FROM ai_readiness WHERE provider='claude_cli'")).mappings().first()
            if (cache is None or cache['status'] != 'ready' or cache['model'] != request.model
                    or cache['checked_at'] <= time.time() - 300):
                raise Conflict('AI_CONNECTION_CHECK_REQUIRED')
        row = connection.execute(text('SELECT * FROM ai_policy WHERE id=1')).mappings().one()
        if row['revision'] != request.expected_revision:
            raise Conflict('AI_REVISION_CONFLICT')
        models = json.loads(row['models_json'])
        models[request.active_provider] = request.model
        connection.execute(text('UPDATE ai_policy SET active_provider=:provider, models_json=:models, revision=revision+1, updated_at=:now WHERE id=1'),
            {'provider':request.active_provider, 'models':json.dumps(models), 'now':time.time()})
    return policy(repository)


def invalidate_key_status(repository):
    with repository.write() as connection:
        connection.execute(text("DELETE FROM ai_readiness WHERE provider='gemini_api'"))

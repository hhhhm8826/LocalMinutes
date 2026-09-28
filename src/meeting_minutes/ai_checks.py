"""Explicit bounded owner checks; polling never performs a remote model request."""
import time

from sqlalchemy import text

from .ai_common import AIFailure
from .ai_lock import provider_lock
from .ai_runtime import GenerationRuntime, create_provider
from .ai_snapshot import configuration
from .ai_secrets import GeminiSecretStore, SecretStoreError


def check_provider(repository, settings, provider, model):
    if provider == 'codex_cli':
        from .ai_policy import readiness
        with provider_lock(settings, provider, lambda: None, wait_seconds=5):
            return readiness(repository, settings, provider, model)
    if provider == 'claude_cli':
        try:
            with provider_lock(settings,provider,lambda:None,wait_seconds=5):
                with repository.write() as connection:
                    row=connection.execute(text("SELECT * FROM ai_readiness WHERE provider='claude_cli'")).mappings().first()
                    if row and row['checked_at']>time.time()-10:
                        return {'ready':row['status']=='ready' and row['model']==model,'code':row['code'] if row['model']==model else 'AI_CHECK_COOLDOWN','checked_at':row['checked_at']}
                    config=configuration(settings, {}, provider, model).model_copy(update={'timeout_seconds':10})
                    connection.execute(text("INSERT OR REPLACE INTO ai_readiness VALUES ('claude_cli',:model,NULL,'checking','AI_CONNECTION_CHECK_REQUIRED',:now)"),{'model':model,'now':time.time()})
                try:
                    result=create_provider(GenerationRuntime(settings,config)).check()
                except AIFailure as exc:
                    result={'ready':False,'code':exc.code,'checked_at':time.time()}
                with repository.write() as connection:
                    connection.execute(text("UPDATE ai_readiness SET status=:status,code=:code,checked_at=:now WHERE provider='claude_cli'"),
                        {'status':'ready' if result['ready'] else 'unavailable','code':result['code'],'now':result['checked_at']})
                return result
        except AIFailure as exc:
            return {'ready':False,'code':exc.code,'checked_at':time.time()}
    if provider != 'gemini_api':
        raise AIFailure('AI_CONFIG_INVALID')
    try:
        # Same lock as generation. No DB write transaction spans a remote request.
        with provider_lock(settings, provider, lambda: None, wait_seconds=5):
            credential = GeminiSecretStore(settings).status()
            if not credential['registered']:
                raise AIFailure('AI_AUTH_REQUIRED')
            now = time.time()
            with repository.write() as connection:
                row = connection.execute(text('SELECT * FROM ai_readiness WHERE provider=:provider'), {'provider': provider}).mappings().first()
                if row and row['checked_at'] > now - 10:
                    if row['model'] == model and row['credential_revision'] == credential['credential_revision']:
                        return {'ready': row['status'] == 'ready', 'code': row['code'], 'checked_at': row['checked_at']}
                    raise AIFailure('AI_CHECK_COOLDOWN')
                config = configuration(settings, {}, provider, model).model_copy(update={'timeout_seconds':10})
                # A crash consumes the cooldown and cannot leave an old ready result behind.
                connection.execute(text("""INSERT INTO ai_readiness VALUES (:provider,:model,:revision,'checking','AI_CONNECTION_CHECK_REQUIRED',:now)
                    ON CONFLICT(provider) DO UPDATE SET model=:model,credential_revision=:revision,status='checking',code='AI_CONNECTION_CHECK_REQUIRED',checked_at=:now"""),
                    {'provider':provider, 'model':model, 'revision':credential['credential_revision'], 'now':now})
            result = {'ready': False, 'code': None, 'checked_at': time.time()}
            try:
                runtime = GenerationRuntime(settings, config)
                result.update(create_provider(runtime).check())
            except AIFailure as exc:
                result['code'] = exc.code
                result['diagnostic'] = exc.diagnostic
            # Discard completion if credentials changed during the check; no stale ready cache.
            current = GeminiSecretStore(settings).status()
            if current['credential_revision'] != credential['credential_revision']:
                result.update(ready=False, code='AI_CREDENTIALS_CHANGED')
            with repository.write() as connection:
                connection.execute(text("""UPDATE ai_readiness SET status=:status,code=:code,checked_at=:now
                    WHERE provider=:provider AND credential_revision=:revision AND model=:model"""),
                    {'status':'ready' if result['ready'] else 'unavailable', 'code':result['code'], 'now':result['checked_at'],
                     'provider':provider, 'revision':credential['credential_revision'], 'model':model})
            return result
    except (AIFailure, SecretStoreError) as exc:
        return {'ready': False, 'code': exc.code, 'checked_at': time.time()}

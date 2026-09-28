"""Bounded retries whose reservations survive process restarts."""
import json
import time

from sqlalchemy import text

from .ai_common import AIFailure
from .ai_lock import provider_lock
from .ai_runtime import GenerationRuntime
from .minutes_storage import finish_call, reserve_call


def attempts(repository, job, purpose):
    with repository.engine.connect() as connection:
        values = connection.execute(text("SELECT metrics_json FROM usage_records WHERE job_id=:job AND stage='SUMMARIZE'"),
                                    {'job': job['id']}).scalars().all()
    return sum(json.loads(value).get('purpose') == purpose for value in values)


def backoff(repository, job, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with repository.engine.connect() as connection:
            repository.assert_current(connection, job)
        time.sleep(min(.1, max(0, deadline - time.monotonic())))


def call_with_retries(repository, settings, job, provider, payload, schema, validate, fingerprint, *, runtime=None):
    runtime = runtime or GenerationRuntime(settings)
    raw_config = job.get('ai_config_json')
    provider_id = json.loads(raw_config)['provider'] if raw_config else 'codex_cli'
    def check_current():
        with repository.engine.connect() as connection:
            repository.assert_current(connection, job)
    purpose, limit, call_payload = 'initial', None, payload
    while True:
        reservation = None
        metrics = {}
        def reserve():
            nonlocal reservation
            reservation = reserve_call(repository, job, runtime.max_calls, fingerprint,
                                       purpose=purpose, purpose_limit=limit,
                                       token_estimate=runtime.input_bytes + runtime.budget(schema)['output_reserve_tokens'])
        try:
            with provider_lock(settings, provider_id, check_current, wait_seconds=runtime.timeout_seconds + 30):
                try:
                    value, metrics = provider.generate(call_payload, schema, reserve)
                finally:
                    if reservation:
                        finish_call(repository, reservation, 'RETURNED', metrics)
            validated = validate(value)
            finish_call(repository, reservation, 'VALIDATED', metrics)
            return validated
        except AIFailure as exc:
            if reservation:
                finish_call(repository, reservation, 'FAILED', {**metrics, 'error_codes': [exc.code], 'diagnostic': exc.diagnostic})
            else:
                raise
            if exc.code in {'CODEX_INVALID_JSON', 'AI_INVALID_JSON', 'MINUTES_SCHEMA_INVALID'} and attempts(repository, job, 'schema_repair') < 1:
                purpose, limit = 'schema_repair', 1
                call_payload = dict(payload, output_validation={'error': exc.code, 'required': 'complete schema-conforming JSON'})
                continue
            # Quota/rate-limit failures require a later deliberate retry, not a burst.
            if provider_id == 'gemini_api' and exc.code == 'AI_RATE_LIMIT':
                raise
            retries = attempts(repository, job, 'network_retry')
            if exc.code in {'CODEX_NETWORK', 'AI_NETWORK', 'AI_RATE_LIMIT', 'AI_SERVICE_UNAVAILABLE', 'AI_SERVER_ERROR'} and retries < 2:
                delay = max(2 ** retries, exc.diagnostic.get('retry_after_seconds', 0))
                if delay > 30:
                    raise
                backoff(repository, job, delay)
                purpose, limit = 'network_retry', 2
                continue
            raise

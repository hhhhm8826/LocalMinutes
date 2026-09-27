"""Bounded retries whose reservations survive process restarts."""
import json
import time

from sqlalchemy import text

from .codex_provider import CodexFailure
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


def call_with_retries(repository, settings, job, provider, payload, schema, validate, fingerprint):
    purpose, limit, call_payload = 'initial', None, payload
    while True:
        reservation = None
        def reserve():
            nonlocal reservation
            reservation = reserve_call(repository, job, settings.codex_max_calls, fingerprint,
                                       purpose=purpose, purpose_limit=limit)
        try:
            value, metrics = provider.generate(call_payload, schema, reserve)
            validated = validate(value)
            finish_call(repository, reservation, 'VALIDATED', metrics)
            return validated
        except CodexFailure as exc:
            if reservation:
                finish_call(repository, reservation, 'FAILED', {'error_codes': [exc.code]})
            else:
                raise
            if exc.code in {'CODEX_INVALID_JSON', 'MINUTES_SCHEMA_INVALID'} and attempts(repository, job, 'schema_repair') < 1:
                purpose, limit = 'schema_repair', 1
                call_payload = dict(payload, output_validation={'error': exc.code, 'required': 'complete schema-conforming JSON'})
                continue
            retries = attempts(repository, job, 'network_retry')
            if exc.code == 'CODEX_NETWORK' and retries < 2:
                backoff(repository, job, 2 ** retries)
                purpose, limit = 'network_retry', 2
                continue
            raise

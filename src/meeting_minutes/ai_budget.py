"""Per-provider weekly accounting. KST Monday reset; no credential or content storage."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time

from pydantic import Field
from sqlalchemy import text
from .contracts import Contract
from .ai_common import AIFailure
from .repository import Conflict


class BudgetUpdate(Contract):
    expected_revision: int = Field(ge=1)
    token_limit: int = Field(strict=True, ge=1, le=1000000000000)


def week(now=None):
    local = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo('Asia/Seoul'))
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=7)).timestamp()


def status(connection, provider='codex_cli', now=None):
    if provider == 'gemini_api':
        from .gemini_budget import status as requests_status
        return requests_status(connection, now)
    start, end = week(now)
    policy = connection.execute(text('SELECT * FROM ai_provider_budget_policy WHERE provider=:provider'), {'provider':provider}).mappings().one()
    rows = connection.execute(text('SELECT status, SUM(tokens) AS tokens FROM ai_budget_ledger WHERE week_start=:week AND provider=:provider GROUP BY status'), {'week':start,'provider':provider}).mappings().all()
    used = sum(row['tokens'] for row in rows if row['status'] == 'ACTUAL')
    reserved = sum(row['tokens'] for row in rows if row['status'] != 'ACTUAL')
    return {'provider':provider, 'token_limit':policy['token_limit'], 'revision':policy['revision'], 'used_tokens':used,
            'reserved_tokens':reserved, 'remaining_tokens':max(0, policy['token_limit']-used-reserved),
            'exhausted':used+reserved >= policy['token_limit'], 'week_start':start, 'refills_at':end}


def get_status(repository, provider='codex_cli'):
    with repository.engine.connect() as connection:
        return status(connection, provider)


def update(repository, request, provider='codex_cli'):
    from .gemini_budget import RequestBudgetUpdate, update as update_requests
    if provider == 'gemini_api' and isinstance(request, RequestBudgetUpdate):
        return update_requests(repository, request)
    if provider == 'gemini_api' or not isinstance(request, BudgetUpdate):
        raise Conflict('AI_BUDGET_UNIT_MISMATCH')
    with repository.write() as connection:
        row = status(connection, provider)
        if row['revision'] != request.expected_revision:
            raise Conflict('AI_BUDGET_REVISION_CONFLICT')
        connection.execute(text('UPDATE ai_provider_budget_policy SET token_limit=:limit,revision=revision+1 WHERE provider=:provider'), {'limit':request.token_limit,'provider':provider})
        return status(connection, provider)


def reserve(connection, record_id, provider, estimate):
    current = status(connection, provider)
    if provider == 'gemini_api':
        from .gemini_budget import reserve as reserve_request
        reserve_request(connection, record_id)
    elif current['exhausted']:
        raise AIFailure('AI_WEEKLY_BUDGET_EXHAUSTED')
    # Caller holds the provider generation flock through settlement. One call may
    # exceed remaining budget; no other call for this provider can start in that interval.
    connection.execute(text('INSERT INTO ai_budget_ledger VALUES (:id,:week,:tokens,:status,:provider)'),
        {'id':record_id,'week':week()[0],'tokens':max(1,estimate),'status':'RESERVED','provider':provider})


def token_total(provider, usage):
    def number(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    if provider == 'gemini_api':
        total = number('total_token_count')
        if total is not None:
            return total
        keys = ('prompt_token_count','candidates_token_count')
        extra = ('thoughts_token_count','tool_use_prompt_token_count')
    else:
        keys = ('input_tokens','output_tokens')
        extra = ('cache_creation_input_tokens','cache_read_input_tokens') if provider == 'claude_cli' else ()
    values = [number(key) for key in keys]
    if any(value is None for value in values):
        return None
    # Codex cached_input_tokens is already contained in input_tokens.
    return sum(values) + sum(number(key) or 0 for key in extra)


def settle(connection, record_id, usage):
    row = connection.execute(text('SELECT * FROM ai_budget_ledger WHERE id=:id'), {'id':record_id}).mappings().first()
    if not row or row['status'] == 'ACTUAL':
        return
    total = token_total(row['provider'],usage) if isinstance(usage,dict) else None
    if total is None:
        connection.execute(text("UPDATE ai_budget_ledger SET status='UNKNOWN' WHERE id=:id"), {'id':record_id})
    else:
        connection.execute(text("UPDATE ai_budget_ledger SET tokens=:tokens,status='ACTUAL' WHERE id=:id"), {'id':record_id,'tokens':total})

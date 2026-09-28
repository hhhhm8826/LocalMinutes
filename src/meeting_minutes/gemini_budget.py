"""Conservative local request allowance; not a claim about Google's project quota."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
from pydantic import Field
from sqlalchemy import text
from .contracts import Contract
from .ai_common import AIFailure
from .repository import Conflict


class RequestBudgetUpdate(Contract):
    expected_revision: int = Field(ge=1)
    request_limit: int = Field(strict=True, ge=1, le=1000000)
    requests_per_minute: int = Field(strict=True, ge=1, le=10000)


def status(connection, now=None):
    now = time.time() if now is None else now
    start = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1)).timestamp()
    policy = dict(connection.execute(text('SELECT * FROM gemini_request_policy WHERE id=1')).mappings().one())
    used = connection.execute(text('SELECT COUNT(*) FROM gemini_request_ledger WHERE created_at>=:start AND created_at<:end'), {'start':start.timestamp(),'end':end}).scalar_one()
    recent = list(connection.execute(text('SELECT created_at FROM gemini_request_ledger WHERE created_at>:cutoff ORDER BY created_at'), {'cutoff':now-60}).scalars())
    from .ai_budget import week
    tokens = connection.execute(text("SELECT status,SUM(tokens) AS tokens FROM ai_budget_ledger WHERE provider='gemini_api' AND week_start=:week GROUP BY status"), {'week':week(now)[0]}).mappings().all()
    daily = used >= policy['request_limit']
    minute = len(recent) >= policy['requests_per_minute']
    available = recent[-policy['requests_per_minute']] + 60 if minute else now
    return {'used_tokens':sum(row['tokens'] for row in tokens if row['status']=='ACTUAL'),
            'reserved_tokens':sum(row['tokens'] for row in tokens if row['status']!='ACTUAL'),
            'mode':'daily_requests', 'provider':'gemini_api', 'revision':policy['revision'],
            'request_limit':policy['request_limit'], 'requests_per_minute':policy['requests_per_minute'],
            'used_requests':used, 'remaining_requests':max(0,policy['request_limit']-used),
            'day_start':start.timestamp(), 'refills_at':end, 'available_at':end if daily else available,
            'exhausted':daily or minute, 'daily_exhausted':daily, 'minute_exhausted':minute}


def update(repository, request):
    with repository.write() as connection:
        current = status(connection)
        if current['revision'] != request.expected_revision:
            raise Conflict('AI_BUDGET_REVISION_CONFLICT')
        connection.execute(text('UPDATE gemini_request_policy SET request_limit=:daily, requests_per_minute=:minute, revision=revision+1 WHERE id=1'),
            {'daily':request.request_limit,'minute':request.requests_per_minute})
        return status(connection)


def reserve(connection, record_id):
    current = status(connection)
    if current['daily_exhausted']:
        raise AIFailure('AI_DAILY_REQUEST_BUDGET_EXHAUSTED')
    if current['minute_exhausted']:
        raise AIFailure('AI_REQUEST_RATE_WAIT')
    connection.execute(text('INSERT INTO gemini_request_ledger VALUES (:id,:now)'), {'id':record_id,'now':time.time()})

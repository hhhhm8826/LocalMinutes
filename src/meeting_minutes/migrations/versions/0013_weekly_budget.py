"""Durable app-wide budget, independent of deletable meeting history."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import text
from alembic import op
revision = '0013'
down_revision = '0012'
branch_labels = depends_on = None


def upgrade():
    op.execute("CREATE TABLE ai_budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), token_limit INTEGER NOT NULL, revision INTEGER NOT NULL)")
    op.execute("INSERT INTO ai_budget_policy VALUES (1,20000000,1)")
    op.execute("CREATE TABLE ai_budget_ledger (id TEXT PRIMARY KEY, week_start REAL NOT NULL, tokens INTEGER NOT NULL, status TEXT NOT NULL, provider TEXT NOT NULL)")
    op.execute("CREATE INDEX ai_budget_week ON ai_budget_ledger(week_start)")

    connection = op.get_bind()
    for row in connection.execute(text("SELECT id,metrics_json,created_at FROM usage_records WHERE stage='SUMMARIZE'")).mappings().all():
        value = json.loads(row['metrics_json'])
        if not value.get('call_reserved'):
            continue
        usage = value.get('usage', {})
        provider = value.get('provider', 'codex_cli')
        def number(key):
            n = usage.get(key) if isinstance(usage,dict) else None
            return n if type(n) is int and n >= 0 else None
        if provider == 'gemini_api':
            total = number('total_token_count')
            keys = ('prompt_token_count','candidates_token_count')
            extra = ('thoughts_token_count','tool_use_prompt_token_count')
        else:
            total = None
            keys = ('input_tokens','output_tokens')
            extra = ('cache_creation_input_tokens','cache_read_input_tokens') if provider == 'claude_cli' else ()
        if total is None and all(number(key) is not None for key in keys):
            total = sum(number(key) for key in keys) + sum(number(key) or 0 for key in extra)
        date = datetime.fromtimestamp(row['created_at'], ZoneInfo('Asia/Seoul'))
        start = (date-timedelta(days=date.weekday())).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        connection.execute(text('INSERT INTO ai_budget_ledger VALUES (:id,:week,:tokens,:status,:provider)'),
            {'id':row['id'],'week':start,'tokens':total if total is not None else 215536,
             'status':'ACTUAL' if total is not None else 'UNKNOWN','provider':provider})


def downgrade():
    raise RuntimeError('Budget history must not be discarded automatically.')

"""Gemini daily requests replace weekly token gating; preserve token history."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from alembic import op
import sqlalchemy as sa
revision = '0016'
down_revision = '0015'
branch_labels = depends_on = None


def upgrade():
    op.execute('CREATE TABLE gemini_request_policy (id INTEGER PRIMARY KEY CHECK(id=1), request_limit INTEGER NOT NULL, requests_per_minute INTEGER NOT NULL, revision INTEGER NOT NULL)')
    op.execute('INSERT INTO gemini_request_policy VALUES (1,10,2,1)')
    op.execute('CREATE TABLE gemini_request_ledger (id TEXT PRIMARY KEY, created_at REAL NOT NULL)')
    op.execute('CREATE INDEX gemini_request_time ON gemini_request_ledger(created_at)')
    # Unknown historical timestamps are conservatively assigned to migration day.
    from meeting_minutes.ai_budget import week
    now = time.time()
    day_start = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    connection = op.get_bind()
    connection.execute(sa.text("INSERT INTO gemini_request_ledger SELECT b.id, COALESCE(u.created_at,:now) FROM ai_budget_ledger b LEFT JOIN usage_records u ON u.id=b.id WHERE b.provider='gemini_api' AND (u.created_at>=:day OR (u.created_at IS NULL AND b.week_start>=:week))"), {'now':now,'day':day_start,'week':week(day_start)[0]})


def downgrade():
    raise RuntimeError('Request reservations must not be discarded automatically.')

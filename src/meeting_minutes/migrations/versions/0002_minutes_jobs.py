"""Persist generation snapshots and completion IDs across worker retries."""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN request_json TEXT NOT NULL DEFAULT '{}'")
    op.execute('ALTER TABLE jobs ADD COLUMN minutes_result_id TEXT REFERENCES minutes_revisions(id)')


def downgrade():
    raise RuntimeError('회의 데이터 보호를 위해 자동 하향 마이그레이션을 지원하지 않습니다')

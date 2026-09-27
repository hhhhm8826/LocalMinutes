"""Remember idempotency keys even when a generation reuses an earlier job."""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE job_requests (
        key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id))''')


def downgrade():
    raise RuntimeError('회의 데이터 보호를 위해 자동 하향 마이그레이션을 지원하지 않습니다')

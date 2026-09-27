"""Ordinary loopback sessions are independent of owner capabilities."""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE local_sessions (
        token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, expires_at REAL NOT NULL)''')
    op.execute('''CREATE TABLE meeting_requests (
        key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE)''')


def downgrade():
    raise RuntimeError('Session and request identities cannot be discarded automatically')

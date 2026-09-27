"""Unified media/transcript expiry with one-time grace for existing inputs."""
import time

from alembic import op
from sqlalchemy import text

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    old = connection.execute(text('SELECT * FROM retention_policy WHERE id=1')).mappings().one()
    untouched = old['revision'] == 1 and not old['enabled'] and all(
        old[key] is None for key in ('original_days', 'audio_days', 'text_days'))
    media_days = None if old['original_days'] is None or old['audio_days'] is None else max(old['original_days'], old['audio_days'])
    op.execute('''CREATE TABLE retention_policy_new (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
        media_days INTEGER, transcript_days INTEGER)''')
    connection.execute(text('INSERT INTO retention_policy_new VALUES (1,:revision,:enabled,:media,:transcript)'),
        {'revision': old['revision'], 'enabled': 1 if untouched else old['enabled'],
         'media': 7 if untouched else media_days, 'transcript': 30 if untouched else old['text_days']})
    op.execute('DROP TABLE retention_policy')
    op.execute('ALTER TABLE retention_policy_new RENAME TO retention_policy')
    for name in ('input_received_at', 'retention_grace_at', 'media_expired_at', 'transcript_expired_at'):
        op.execute(f'ALTER TABLE meetings ADD COLUMN {name} REAL')
    connection.execute(text('''UPDATE meetings SET
        input_received_at=(SELECT MIN(created_at) FROM media_assets WHERE meeting_id=meetings.id),
        retention_grace_at=:now'''), {'now': time.time()})
    op.execute('''CREATE TABLE document_snapshots (
        result_id TEXT PRIMARY KEY REFERENCES minutes_revisions(id) ON DELETE CASCADE,
        content_json TEXT NOT NULL)''')


def downgrade():
    raise RuntimeError('Retention grace and expiration history must be preserved')

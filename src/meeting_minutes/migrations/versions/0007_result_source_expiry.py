"""A result outlives its source; keep provenance in its immutable JSON."""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE TEMP TABLE result_job_links AS SELECT id,minutes_result_id FROM jobs WHERE minutes_result_id IS NOT NULL')
    op.execute('UPDATE jobs SET minutes_result_id=NULL WHERE minutes_result_id IS NOT NULL')
    op.execute('''CREATE TABLE minutes_revisions_new (
        id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
        transcript_version TEXT REFERENCES transcript_versions(id) ON DELETE SET NULL,
        parent_id TEXT REFERENCES minutes_revisions_new(id), content_json TEXT NOT NULL,
        created_at REAL NOT NULL)''')
    op.execute('INSERT INTO minutes_revisions_new SELECT * FROM minutes_revisions')
    # The replacement already preserves every parent. Clear only old-table
    # self references before dropping it under enabled SQLite FK enforcement.
    op.execute('UPDATE minutes_revisions SET parent_id=NULL')
    op.execute('DROP TABLE minutes_revisions')
    op.execute('ALTER TABLE minutes_revisions_new RENAME TO minutes_revisions')
    op.execute('''UPDATE jobs SET minutes_result_id=(SELECT minutes_result_id FROM result_job_links WHERE result_job_links.id=jobs.id)
        WHERE id IN (SELECT id FROM result_job_links)''')
    op.execute('DROP TABLE result_job_links')


def downgrade():
    raise RuntimeError('Expired result sources cannot be recreated')

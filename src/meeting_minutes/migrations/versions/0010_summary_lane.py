"""Independent text-summary slot and current result pointer repair."""
import json
from alembic import op
from sqlalchemy import text

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('DROP INDEX one_active_job')
    op.execute("""CREATE UNIQUE INDEX one_active_job_per_lane ON jobs
        ((CASE WHEN kind='summarize' THEN 'summary' ELSE 'analysis' END))
        WHERE state IN ('RUNNING','CANCEL_REQUESTED')""")
    connection = op.get_bind()
    # Promote only a completed latest child of the still-current parent.
    # Concurrent edits or transcript changes must never be overwritten.
    rows = connection.execute(text("""SELECT m.id,m.minutes_revision,m.transcript_version,
        v.id AS candidate,v.parent_id,v.transcript_version AS source
        FROM meetings m JOIN minutes_revisions v ON v.meeting_id=m.id
        JOIN jobs j ON j.minutes_result_id=v.id AND j.state='COMPLETED'
        WHERE m.deleted_at IS NULL AND v.parent_id=m.minutes_revision
        AND v.transcript_version=m.transcript_version
        AND json_extract(v.content_json,'$.revision')=(SELECT MAX(json_extract(x.content_json,'$.revision'))
            FROM minutes_revisions x WHERE x.meeting_id=m.id)""")).mappings().all()
    for row in rows:
        connection.execute(text('UPDATE meetings SET minutes_revision=:candidate,revision=revision+1 WHERE id=:id'), dict(row))
    for row in connection.execute(text("SELECT id,settings_json,source_metadata_json FROM meetings WHERE source_kind='youtube'" )).mappings().all():
        settings = json.loads(row['settings_json'])
        source = json.loads(row['source_metadata_json'])
        if settings.get('title') in ('', 'YouTube 영상', None) and source.get('title'):
            settings['title'] = source['title']
            connection.execute(text('UPDATE meetings SET title=:title,settings_json=:settings WHERE id=:id'),
                {'id':row['id'], 'title':source['title'], 'settings':json.dumps(settings,ensure_ascii=False)})


def downgrade():
    raise RuntimeError('Independent active queues must be drained before downgrade')

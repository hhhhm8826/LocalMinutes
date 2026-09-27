"""Keep document purpose independent from acquisition method."""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE meetings ADD COLUMN document_kind TEXT NOT NULL DEFAULT 'meeting' CHECK(document_kind IN ('meeting','video_summary'))")
    op.execute("ALTER TABLE meetings ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'file' CHECK(source_kind IN ('file','youtube'))")
    op.execute("ALTER TABLE meetings ADD COLUMN source_metadata_json TEXT NOT NULL DEFAULT '{}'")


def downgrade():
    raise RuntimeError('Document identities cannot be discarded automatically')

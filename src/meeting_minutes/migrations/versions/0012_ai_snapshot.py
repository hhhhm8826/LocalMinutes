"""Keep provider policy snapshots separate from mutable job/transcript state."""
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE jobs ADD COLUMN ai_config_json TEXT')


def downgrade():
    raise RuntimeError('Job AI configuration must not be discarded automatically.')

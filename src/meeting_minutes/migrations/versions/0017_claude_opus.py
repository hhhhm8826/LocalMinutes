"""Move the previous Claude default to Opus 5.5; preserve explicit custom models."""
import json
import time
from alembic import op
import sqlalchemy as sa
revision = '0017'
down_revision = '0016'
branch_labels = depends_on = None


def upgrade():
    c = op.get_bind()
    row = c.execute(sa.text('SELECT models_json FROM ai_policy WHERE id=1')).first()
    if row:
        models = json.loads(row[0])
        if models.get('claude_cli') == 'claude-sonnet-4-6':
            models['claude_cli'] = 'claude-opus-5-5'
            c.execute(sa.text('UPDATE ai_policy SET models_json=:models,revision=revision+1,updated_at=:now WHERE id=1'), {'models':json.dumps(models),'now':time.time()})
    c.execute(sa.text("DELETE FROM ai_readiness WHERE provider='claude_cli'"))


def downgrade():
    raise RuntimeError('Do not rewrite captured model choices automatically.')

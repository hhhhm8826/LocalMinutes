"""Use Gemini3.8 for future jobs; never rewrite captured job configuration."""
import json
import time
from alembic import op
import sqlalchemy as sa
revision = '0015'
down_revision = '0014'
branch_labels = depends_on = None


def upgrade():
    connection=op.get_bind()
    row=connection.execute(sa.text('SELECT models_json FROM ai_policy WHERE id=1')).first()
    if row:
        models=json.loads(row[0])
        if models.get('gemini_api')=='gemini-3.5-flash':
            models['gemini_api']='gemini-3.8-flash'
            connection.execute(sa.text('UPDATE ai_policy SET models_json=:models,revision=revision+1,updated_at=:now WHERE id=1'),
                               {'models':json.dumps(models),'now':time.time()})
    connection.execute(sa.text("DELETE FROM ai_readiness WHERE provider='gemini_api'"))


def downgrade():
    raise RuntimeError('Gemini job snapshots must not be rewritten automatically.')

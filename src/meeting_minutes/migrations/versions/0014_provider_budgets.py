"""Split limits by provider while preserving all ledger entries."""
from alembic import op
revision = '0014'
down_revision = '0013'
branch_labels = depends_on = None


def upgrade():
    op.execute("CREATE TABLE ai_provider_budget_policy (provider TEXT PRIMARY KEY, token_limit INTEGER NOT NULL, revision INTEGER NOT NULL)")
    for provider in ('codex_cli','claude_cli'):
        op.execute(f"INSERT INTO ai_provider_budget_policy SELECT '{provider}',token_limit,1 FROM ai_budget_policy WHERE id=1")
    op.execute("INSERT INTO ai_provider_budget_policy VALUES ('gemini_api',2000000,1)")
    op.execute("CREATE INDEX ai_budget_provider_week ON ai_budget_ledger(provider,week_start)")
    op.execute("DROP TABLE ai_budget_policy")


def downgrade():
    raise RuntimeError('Provider budgets must not be merged automatically.')

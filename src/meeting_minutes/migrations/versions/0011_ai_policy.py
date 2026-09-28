"""비밀 없는 전역 AI 정책과 제한된 준비 상태 캐시입니다."""
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE ai_policy (
        id INTEGER PRIMARY KEY CHECK(id=1), active_provider TEXT NOT NULL DEFAULT 'codex_cli'
        CHECK(active_provider IN ('codex_cli','gemini_api','claude_cli')),
        models_json TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1), updated_at REAL NOT NULL)""")
    op.execute("""CREATE TABLE ai_readiness (
        provider TEXT PRIMARY KEY, model TEXT NOT NULL, credential_revision TEXT,
        status TEXT NOT NULL, code TEXT, checked_at REAL NOT NULL)""")


def downgrade():
    raise RuntimeError('AI 정책은 자동 삭제하지 않습니다.')

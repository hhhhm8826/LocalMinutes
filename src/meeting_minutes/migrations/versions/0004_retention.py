"""명시적으로 활성화하는 자료별 보관 정책."""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE retention_policy (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
        original_days INTEGER, audio_days INTEGER, text_days INTEGER)''')
    op.execute('INSERT INTO retention_policy VALUES (1,1,0,NULL,NULL,NULL)')


def downgrade():
    raise RuntimeError('보관 정책 자동 하향 마이그레이션은 지원하지 않습니다')

"""시계 보정에 영향받지 않는 부팅 ID와 프로세스 시작 tick."""
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE jobs ADD COLUMN process_identity_json TEXT')


def downgrade():
    raise RuntimeError('실행 중 프로세스 식별 정보를 자동으로 제거하지 않습니다')

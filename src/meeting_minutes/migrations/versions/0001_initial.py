"""영속 작업·불변 버전·소유자 세션의 최초 스키마."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE meetings (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, settings_json TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            deleted_at REAL, transcript_version TEXT, minutes_revision TEXT)""",
        """CREATE TABLE media_assets (
            id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
            original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL, duration_ms INTEGER, tracks_json TEXT, selected_track INTEGER,
            created_at REAL NOT NULL)""",
        """CREATE TABLE jobs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            meeting_id TEXT NOT NULL REFERENCES meetings(id), media_id TEXT REFERENCES media_assets(id),
            kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
            attempt_id TEXT NOT NULL, attempt_number INTEGER NOT NULL DEFAULT 1,
            idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL,
            cancel_requested INTEGER NOT NULL DEFAULT 0, blocked_reason TEXT, error_code TEXT,
            pid INTEGER, process_created_at REAL, progress_json TEXT, transcript_version TEXT)""",
        """CREATE UNIQUE INDEX one_active_job ON jobs ((1))
            WHERE state IN ('RUNNING','CANCEL_REQUESTED')""",
        """CREATE TABLE stage_artifacts (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), stage TEXT NOT NULL,
            attempt_id TEXT NOT NULL, input_fingerprint TEXT NOT NULL, path TEXT NOT NULL,
            sha256 TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE transcript_versions (
            id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
            parent_id TEXT REFERENCES transcript_versions(id), kind TEXT NOT NULL,
            content_json TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE minutes_revisions (
            id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
            transcript_version TEXT NOT NULL REFERENCES transcript_versions(id),
            parent_id TEXT REFERENCES minutes_revisions(id), content_json TEXT NOT NULL,
            created_at REAL NOT NULL)""",
        """CREATE TABLE usage_records (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), attempt_id TEXT NOT NULL,
            stage TEXT NOT NULL, metrics_json TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE owner_sessions (
            token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, expires_at REAL NOT NULL)""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("회의 데이터 보호를 위해 자동 하향 마이그레이션을 지원하지 않습니다")

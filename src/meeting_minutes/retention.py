"""Expire media and source text; result documents and versions are permanent."""
import json
import time
from datetime import datetime, timezone

from pydantic import Field
from sqlalchemy import text

from .contracts import Contract
from .deletion import meeting_file_lock
from .files import safe_file, sync_directory
from .repository import Conflict
from .documents import legacy_document
from .temporary import cleanup_job_temporary


class RetentionUpdate(Contract):
    expected_revision: int = Field(ge=1)
    enabled: bool
    media_days: int | None = Field(default=7, ge=1, le=36500)
    transcript_days: int | None = Field(default=30, ge=1, le=36500)


def policy(repository):
    with repository.engine.connect() as connection:
        result = dict(connection.execute(text('SELECT * FROM retention_policy WHERE id=1')).mappings().one())
    result.pop('id')
    result['enabled'] = bool(result['enabled'])
    return result


def update_policy(repository, request):
    with repository.write() as connection:
        changed = connection.execute(text('''UPDATE retention_policy SET enabled=:enabled, media_days=:media_days,
            transcript_days=:transcript_days,revision=revision+1 WHERE id=1 AND revision=:expected_revision'''),
            request.model_dump()).rowcount
        if changed != 1:
            raise Conflict('REVISION_CONFLICT')
    return policy(repository)


def expire_meeting(repository, settings, meeting_id, now):
    with meeting_file_lock(settings, meeting_id, exclusive=True), repository.write() as connection:
        current = connection.execute(text('SELECT * FROM retention_policy WHERE id=1')).mappings().one()
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL'),
                                     {'id': meeting_id}).mappings().first()
        if not current['enabled'] or not meeting:
            return
        jobs = connection.execute(text('SELECT * FROM jobs WHERE meeting_id=:id'), {'id': meeting_id}).mappings().all()
        if any(job['pid'] is not None or job['state'] in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'}
               or (job['state'] == 'BLOCKED' and job['blocked_reason'] in {'AI_WEEKLY_BUDGET_EXHAUSTED', 'AI_DAILY_REQUEST_BUDGET_EXHAUSTED', 'AI_REQUEST_RATE_WAIT'}) for job in jobs):
            return
        def expired(field):
            anchor = meeting['input_received_at']
            if anchor is None:
                return False
            anchor = max(anchor, meeting['retention_grace_at'] or anchor)
            return current[field] is not None and anchor + current[field] * 86400 <= now
        media, content = expired('media_days'), expired('transcript_days')
        if media:
            for row in connection.execute(text('SELECT stored_name FROM media_assets WHERE meeting_id=:id'), {'id': meeting_id}):
                safe_file(settings.data_dir / 'media', row[0]).unlink(missing_ok=True)
            for path in (settings.data_dir / 'media').glob(meeting_id + '-*'):
                safe_file(path.parent, path.name).unlink(missing_ok=True)
            sync_directory(settings.data_dir / 'media')
        if not media and not content:
            return
        root = settings.data_dir / 'artifacts'
        for job in jobs:
            artifacts = connection.execute(text('SELECT id,path,stage FROM stage_artifacts WHERE job_id=:id'),
                                           {'id': job['id']}).mappings().all()
            for artifact in artifacts:
                if (media and artifact['stage'] == 'EXTRACT') or (content and artifact['stage'] != 'EXTRACT'):
                    safe_file(root, artifact['path']).unlink(missing_ok=True)
                    connection.execute(text('DELETE FROM stage_artifacts WHERE id=:id'), {'id': artifact['id']})
            # 중단된 저장의 미등록 JSON/결과도 텍스트 보관 범위에 포함한다.
            if media:
                for path in root.glob(job['id'] + '-*.wav*'):
                    safe_file(root, path.name).unlink(missing_ok=True)
            if content:
                cleanup_job_temporary(job['id'])
                for path in root.glob(job['id'] + '-*'):
                    if '.json' in path.name or '.result' in path.name:
                        safe_file(root, path.name).unlink(missing_ok=True)
        sync_directory(root)
        if media:
            connection.execute(text('UPDATE meetings SET media_expired_at=COALESCE(media_expired_at,:now) WHERE id=:id'),
                               {'id': meeting_id, 'now': now})
        if content:
            # Preserve derived legacy positions/owner labels before source expiry.
            # The immutable original document JSON is never rewritten.
            for result in connection.execute(text('SELECT * FROM minutes_revisions WHERE meeting_id=:id'),
                                              {'id': meeting_id}).mappings().all():
                value = json.loads(result['content_json'])
                if value.get('schema_version', 1) == 1:
                    raw = connection.execute(text('SELECT content_json FROM transcript_versions WHERE id=:id'),
                                             {'id': result['transcript_version']}).scalar_one_or_none()
                    document = legacy_document(value, json.loads(meeting['settings_json']), json.loads(raw) if raw else None,
                        generated_at=datetime.fromtimestamp(result['created_at'], timezone.utc))
                    connection.execute(text('INSERT OR IGNORE INTO document_snapshots VALUES (:id,:content)'),
                                       {'id': result['id'], 'content': document.model_dump_json()})
            connection.execute(text('''UPDATE jobs SET transcript_version=NULL,request_json='{}'
                WHERE meeting_id=:id'''), {'id': meeting_id})
            connection.execute(text('DELETE FROM transcript_versions WHERE meeting_id=:id'), {'id': meeting_id})
            if meeting['transcript_expired_at'] is None:
                connection.execute(text('''UPDATE meetings SET transcript_version=NULL,transcript_expired_at=:now,
                    revision=revision+1,updated_at=:now WHERE id=:id'''), {'id': meeting_id, 'now': now})


def reap_retention(repository, settings, now=None):
    if not policy(repository)['enabled']:
        return
    with repository.engine.connect() as connection:
        ids = list(connection.execute(text('SELECT id FROM meetings WHERE deleted_at IS NULL')).scalars())
    for meeting_id in ids:
        try:
            expire_meeting(repository, settings, meeting_id, now if now is not None else time.time())
        except (OSError, ValueError):
            continue

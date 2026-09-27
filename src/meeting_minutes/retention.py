"""기본은 무기한 보관. 사용자가 켠 정책만 작업 종료 후 적용한다."""
import time

from pydantic import Field
from sqlalchemy import text

from .contracts import Contract
from .deletion import meeting_file_lock
from .files import safe_file, sync_directory
from .repository import Conflict


class RetentionUpdate(Contract):
    expected_revision: int = Field(ge=1)
    enabled: bool
    original_days: int | None = Field(default=None, ge=1, le=36500)
    audio_days: int | None = Field(default=None, ge=1, le=36500)
    text_days: int | None = Field(default=None, ge=1, le=36500)


def policy(repository):
    with repository.engine.connect() as connection:
        result = dict(connection.execute(text('SELECT * FROM retention_policy WHERE id=1')).mappings().one())
    result.pop('id')
    result['enabled'] = bool(result['enabled'])
    return result


def update_policy(repository, request):
    with repository.write() as connection:
        changed = connection.execute(text('''UPDATE retention_policy SET enabled=:enabled, original_days=:original_days,
            audio_days=:audio_days,text_days=:text_days,revision=revision+1 WHERE id=1 AND revision=:expected_revision'''),
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
        if any(job['pid'] is not None or job['state'] in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED', 'BLOCKED', 'INTERRUPTED'} for job in jobs):
            return
        def expired(field):
            return current[field] is not None and meeting['created_at'] + current[field] * 86400 <= now
        if expired('original_days'):
            for row in connection.execute(text('SELECT stored_name FROM media_assets WHERE meeting_id=:id'), {'id': meeting_id}):
                safe_file(settings.data_dir / 'media', row[0]).unlink(missing_ok=True)
            sync_directory(settings.data_dir / 'media')
        audio, content = expired('audio_days'), expired('text_days')
        if not audio and not content:
            return
        root = settings.data_dir / 'artifacts'
        for job in jobs:
            artifacts = connection.execute(text('SELECT id,path,stage FROM stage_artifacts WHERE job_id=:id'),
                                           {'id': job['id']}).mappings().all()
            for artifact in artifacts:
                if (audio and artifact['stage'] == 'EXTRACT') or (content and artifact['stage'] != 'EXTRACT'):
                    safe_file(root, artifact['path']).unlink(missing_ok=True)
                    connection.execute(text('DELETE FROM stage_artifacts WHERE id=:id'), {'id': artifact['id']})
            # 중단된 저장의 미등록 JSON/결과도 텍스트 보관 범위에 포함한다.
            if content:
                for path in root.glob(job['id'] + '-*'):
                    if '.json' in path.name or '.result' in path.name:
                        safe_file(root, path.name).unlink(missing_ok=True)
        sync_directory(root)
        if content:
            connection.execute(text('''UPDATE jobs SET transcript_version=NULL,minutes_result_id=NULL,request_json='{}'
                WHERE meeting_id=:id'''), {'id': meeting_id})
            connection.execute(text('DELETE FROM minutes_revisions WHERE meeting_id=:id'), {'id': meeting_id})
            connection.execute(text('DELETE FROM transcript_versions WHERE meeting_id=:id'), {'id': meeting_id})
            if meeting['transcript_version'] or meeting['minutes_revision']:
                connection.execute(text('''UPDATE meetings SET transcript_version=NULL,minutes_revision=NULL,
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

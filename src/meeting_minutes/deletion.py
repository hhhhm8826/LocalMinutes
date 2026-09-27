"""삭제 요청을 먼저 영속화하고 자식 종료 확인 뒤 자료를 정리한다."""
from contextlib import contextmanager
import fcntl
import os
import re
import time

from sqlalchemy import text

from .files import safe_file, sync_directory
from .repository import Conflict


@contextmanager
def meeting_file_lock(settings, meeting_id, *, exclusive=False):
    if not re.fullmatch(r'[0-9a-f]{32}', meeting_id):
        raise Conflict('INVALID_MEETING_ID')
    root = settings.data_dir / 'locks'
    root.mkdir(exist_ok=True, mode=0o700)
    descriptor = os.open(safe_file(root, meeting_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def request_delete(repository, meeting_id):
    with repository.write() as connection:
        now = time.time()
        connection.execute(text('UPDATE meetings SET deleted_at=COALESCE(deleted_at,:now) WHERE id=:id'),
                           {'id': meeting_id, 'now': now})
        connection.execute(text("""UPDATE jobs SET cancel_requested=1, updated_at=:now,
            state=CASE WHEN state IN ('RUNNING','CANCEL_REQUESTED') THEN 'CANCEL_REQUESTED' ELSE 'CANCELLED' END
            WHERE meeting_id=:id AND state IN ('QUEUED','RUNNING','CANCEL_REQUESTED','BLOCKED','INTERRUPTED','FAILED')"""),
            {'id': meeting_id, 'now': now})


def purge_meeting(repository, settings, meeting_id):
    try:
        with meeting_file_lock(settings, meeting_id, exclusive=True), repository.write() as connection:
            meeting = connection.execute(text('SELECT deleted_at FROM meetings WHERE id=:id'), {'id': meeting_id}).first()
            if not meeting:
                return True
            if meeting.deleted_at is None:
                raise Conflict('DELETION_NOT_REQUESTED')
            jobs = connection.execute(text('SELECT * FROM jobs WHERE meeting_id=:id'), {'id': meeting_id}).mappings().all()
            if any(job['pid'] is not None or job['state'] in {'RUNNING', 'CANCEL_REQUESTED'} for job in jobs):
                return False
            # DB 삭제보다 파일 삭제를 먼저 한다. 중간 실패 시 tombstone/참조가 남아 재시작 후 재시도한다.
            media_root, artifact_root = settings.data_dir / 'media', settings.data_dir / 'artifacts'
            paths = [safe_file(media_root, row[0]) for row in connection.execute(
                text('SELECT stored_name FROM media_assets WHERE meeting_id=:id'), {'id': meeting_id})]
            paths.extend(safe_file(media_root, path.name) for path in media_root.glob(meeting_id + '-*'))
            for job in jobs:
                paths.extend(safe_file(artifact_root, row[0]) for row in connection.execute(
                    text('SELECT path FROM stage_artifacts WHERE job_id=:id'), {'id': job['id']}))
                paths.extend(safe_file(artifact_root, path.name) for path in artifact_root.glob(job['id'] + '-*'))
            for path in set(paths):
                path.unlink(missing_ok=True)
            sync_directory(media_root)
            sync_directory(artifact_root)
            for table in ('job_requests', 'usage_records', 'stage_artifacts'):
                connection.execute(text(f'DELETE FROM {table} WHERE job_id IN (SELECT id FROM jobs WHERE meeting_id=:id)'),
                                   {'id': meeting_id})
            connection.execute(text('DELETE FROM jobs WHERE meeting_id=:id'), {'id': meeting_id})
            connection.execute(text('DELETE FROM minutes_revisions WHERE meeting_id=:id'), {'id': meeting_id})
            connection.execute(text('DELETE FROM transcript_versions WHERE meeting_id=:id'), {'id': meeting_id})
            connection.execute(text('DELETE FROM media_assets WHERE meeting_id=:id'), {'id': meeting_id})
            connection.execute(text('DELETE FROM meetings WHERE id=:id'), {'id': meeting_id})
            return True
    except BlockingIOError:
        return False


def reap_deletions(repository, settings):
    with repository.engine.connect() as connection:
        ids = list(connection.execute(text('SELECT id FROM meetings WHERE deleted_at IS NOT NULL')).scalars())
    for meeting_id in ids:
        try:
            purge_meeting(repository, settings, meeting_id)
        except (OSError, ValueError):
            # 파일 권한/경로 문제는 미완료 상태를 보존하며 다른 회의 처리를 막지 않는다.
            continue

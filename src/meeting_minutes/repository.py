"""짧은 쓰기 트랜잭션으로 작업 등록·선점·상태 전이를 직렬화한다."""
from contextlib import contextmanager
import json
import time
import uuid

from sqlalchemy import text


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def identifier():
    return uuid.uuid4().hex


class Repository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def write(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def meeting(self, meeting_id):
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL"),
                                     {"id": meeting_id}).mappings().first()
        if not row:
            raise Missing("MEETING_NOT_FOUND")
        return dict(row)

    def create_meeting(self, settings):
        now, meeting_id = time.time(), identifier()
        with self.write() as connection:
            connection.execute(text("""INSERT INTO meetings(id,title,settings_json,created_at,updated_at)
                VALUES (:id,:title,:settings,:now,:now)"""),
                {"id": meeting_id, "title": settings.title, "settings": settings.model_dump_json(), "now": now})
        return self.meeting(meeting_id)

    def meetings(self, search=""):
        with self.engine.connect() as connection:
            rows = connection.execute(text("""SELECT m.*,
                (SELECT duration_ms FROM media_assets WHERE meeting_id=m.id LIMIT 1) AS duration_ms,
                (SELECT json_extract(content_json,'$.status') FROM minutes_revisions WHERE id=m.minutes_revision) AS minutes_status
                FROM meetings m WHERE deleted_at IS NULL
                AND (title LIKE :query ESCAPE '\\' OR EXISTS (
                    SELECT 1 FROM transcript_versions t, json_each(t.content_json, '$.segments') s
                    WHERE t.id=m.transcript_version AND json_extract(s.value,'$.text') LIKE :query ESCAPE '\\'
                ) OR EXISTS (
                    SELECT 1 FROM minutes_revisions v, json_tree(v.content_json) n
                    WHERE v.id=m.minutes_revision AND n.key IN ('summary','text','task')
                    AND n.type='text' AND n.value LIKE :query ESCAPE '\\'
                )) ORDER BY created_at DESC LIMIT 200"""),
                {"query": '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'}).mappings()
            return [dict(row) for row in rows]

    def register_media(self, meeting_id, name, stored_name, size, sha256, key, request_hash):
        with self.write() as connection:
            if connection.execute(text('SELECT 1 FROM job_requests WHERE key=:key'), {'key': key}).first():
                raise Conflict('IDEMPOTENCY_CONFLICT')
            row = connection.execute(text("SELECT * FROM jobs WHERE idempotency_key=:key"),
                                     {"key": key}).mappings().first()
            if row:
                if row['request_hash'] != request_hash or row['meeting_id'] != meeting_id:
                    raise Conflict("IDEMPOTENCY_CONFLICT")
                return dict(row), False
            meeting = connection.execute(text("SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL"),
                                         {"id": meeting_id}).mappings().first()
            if not meeting:
                raise Missing("MEETING_NOT_FOUND")
            existing = connection.execute(text("SELECT 1 FROM jobs WHERE meeting_id=:id"), {"id": meeting_id}).first()
            if existing:
                raise Conflict("MEDIA_ALREADY_UPLOADED")
            media_id, job_id, attempt_id, now = identifier(), identifier(), identifier(), time.time()
            connection.execute(text("""INSERT INTO media_assets
                (id,meeting_id,original_name,stored_name,size_bytes,sha256,created_at)
                VALUES (:id,:meeting,:name,:stored,:size,:hash,:now)"""),
                {"id": media_id, "meeting": meeting_id, "name": name, "stored": stored_name,
                 "size": size, "hash": sha256, "now": now})
            connection.execute(text("""INSERT INTO jobs
                (id,meeting_id,media_id,kind,state,stage,attempt_id,idempotency_key,request_hash,created_at,updated_at)
                VALUES (:id,:meeting,:media,'transcribe','QUEUED','VALIDATE',:attempt,:key,:hash,:now,:now)"""),
                {"id": job_id, "meeting": meeting_id, "media": media_id, "attempt": attempt_id,
                 "key": key, "hash": request_hash, "now": now})
            return dict(connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().one()), True

    def job(self, job_id):
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().first()
        if not row:
            raise Missing("JOB_NOT_FOUND")
        return dict(row)

    def jobs(self):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text("SELECT * FROM jobs ORDER BY sequence DESC LIMIT 200")).mappings()]

    def claim(self):
        with self.write() as connection:
            # 차단·중단 head를 건너뛰지 않는다. 실패·완료·취소는 큐를 점유하지 않는다.
            row = connection.execute(text("""SELECT j.* FROM jobs j JOIN meetings m ON m.id=j.meeting_id
                WHERE j.state IN ('QUEUED','RUNNING','CANCEL_REQUESTED','BLOCKED','INTERRUPTED')
                AND m.deleted_at IS NULL ORDER BY j.sequence LIMIT 1""")).mappings().first()
            if not row or row['state'] != 'QUEUED':
                return None
            connection.execute(text("""UPDATE jobs SET state='RUNNING',started_at=:now,updated_at=:now
                WHERE id=:id AND state='QUEUED'"""), {"id": row['id'], "now": time.time()})
            return dict(row) | {'state': 'RUNNING'}

    def cancel(self, job_id):
        with self.write() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {'id': job_id}).mappings().first()
            if not row:
                raise Missing("JOB_NOT_FOUND")
            if row['state'] in {'QUEUED', 'BLOCKED', 'INTERRUPTED', 'FAILED'}:
                state = 'CANCELLED'
            elif row['state'] in {'RUNNING', 'CANCEL_REQUESTED'}:
                state = 'CANCEL_REQUESTED'
            else:
                return dict(row)
            connection.execute(text("""UPDATE jobs SET state=:state,cancel_requested=1,updated_at=:now,
                finished_at=CASE WHEN :state='CANCELLED' THEN :now ELSE finished_at END WHERE id=:id"""),
                {'state': state, 'now': time.time(), 'id': job_id})
        return self.job(job_id)

    def retry(self, job_id):
        with self.write() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {'id': job_id}).mappings().first()
            if not row:
                raise Missing("JOB_NOT_FOUND")
            if row['state'] not in {'FAILED', 'BLOCKED', 'INTERRUPTED', 'CANCELLED'}:
                raise Conflict("JOB_NOT_RETRYABLE")
            if connection.execute(text("SELECT 1 FROM meetings WHERE id=:id AND deleted_at IS NULL"),
                                  {'id': row['meeting_id']}).first() is None:
                raise Missing("MEETING_NOT_FOUND")
            connection.execute(text("""UPDATE jobs SET state='QUEUED',attempt_id=:attempt,
                attempt_number=attempt_number+1,cancel_requested=0,blocked_reason=NULL,error_code=NULL,
                pid=NULL,process_created_at=NULL,process_identity_json=NULL,started_at=NULL,finished_at=NULL,updated_at=:now WHERE id=:id"""),
                {'id': job_id, 'attempt': identifier(), 'now': time.time()})
        return self.job(job_id)

    def finish(self, job_id, attempt_id, state, reason=None):
        if state not in {'COMPLETED', 'COMPLETED_TRANSCRIPT_ONLY', 'BLOCKED', 'FAILED', 'INTERRUPTED', 'CANCELLED'}:
            raise ValueError('invalid terminal state')
        with self.write() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {'id': job_id}).mappings().one()
            if row['attempt_id'] != attempt_id or row['state'] not in {'RUNNING', 'CANCEL_REQUESTED'}:
                return False
            if row['cancel_requested'] and state != 'CANCELLED':
                return False
            connection.execute(text("""UPDATE jobs SET state=:state,updated_at=:now,finished_at=:now,
                blocked_reason=:blocked,error_code=:error,pid=NULL,process_created_at=NULL,process_identity_json=NULL WHERE id=:id"""),
                {'state': state, 'now': time.time(), 'id': job_id,
                 'blocked': reason if state == 'BLOCKED' else None,
                 'error': reason if state in {'FAILED', 'INTERRUPTED'} else None})
            return True

    def set_process(self, job_id, attempt_id, pid, created_at, process_identity=None):
        with self.write() as connection:
            result = connection.execute(text("""UPDATE jobs SET pid=:pid,process_created_at=:created,process_identity_json=:identity
                WHERE id=:id AND attempt_id=:attempt AND state='RUNNING' AND cancel_requested=0"""),
                {'pid': pid, 'created': created_at, 'id': job_id, 'attempt': attempt_id,
                 'identity': json.dumps(process_identity) if process_identity else None})
            return result.rowcount == 1

    def media(self, media_id):
        with self.engine.connect() as connection:
            return dict(connection.execute(text("SELECT * FROM media_assets WHERE id=:id"), {'id': media_id}).mappings().one())

    def set_media_metadata(self, job, duration_ms, tracks):
        with self.write() as connection:
            self.assert_current(connection, job)
            connection.execute(text("UPDATE media_assets SET duration_ms=:duration,tracks_json=:tracks WHERE id=:id"),
                               {'duration': duration_ms, 'tracks': json.dumps(tracks), 'id': job['media_id']})

    def select_track(self, job_id, track):
        with self.write() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {'id': job_id}).mappings().first()
            if not row:
                raise Missing('JOB_NOT_FOUND')
            if row['state'] != 'BLOCKED' or row['blocked_reason'] != 'AUDIO_TRACK_REQUIRED':
                raise Conflict('TRACK_NOT_REQUESTED')
            media = connection.execute(text("SELECT * FROM media_assets WHERE id=:id"), {'id': row['media_id']}).mappings().one()
            if track not in [item['index'] for item in json.loads(media['tracks_json'])]:
                raise Conflict('INVALID_AUDIO_TRACK')
            connection.execute(text("UPDATE media_assets SET selected_track=:track WHERE id=:id"),
                               {'track': track, 'id': media['id']})
            # 선택과 재대기를 한 트랜잭션으로 묶어 실패한 선택이 남지 않게 한다.
            connection.execute(text("""UPDATE jobs SET state='QUEUED',attempt_id=:attempt,
                attempt_number=attempt_number+1,cancel_requested=0,blocked_reason=NULL,error_code=NULL,
                pid=NULL,process_created_at=NULL,process_identity_json=NULL,started_at=NULL,finished_at=NULL,updated_at=:now WHERE id=:id"""),
                {'id': job_id, 'attempt': identifier(), 'now': time.time()})
        return self.job(job_id)

    def assert_current(self, connection, job):
        current = connection.execute(text("""SELECT j.state,j.attempt_id,j.cancel_requested,m.deleted_at
            FROM jobs j JOIN meetings m ON m.id=j.meeting_id WHERE j.id=:id"""), {'id': job['id']}).mappings().one()
        if (current['state'] != 'RUNNING' or current['attempt_id'] != job['attempt_id']
                or current['cancel_requested'] or current['deleted_at'] is not None):
            raise Conflict('STALE_ATTEMPT')

    def stage(self, job, stage, progress=None):
        with self.write() as connection:
            self.assert_current(connection, job)
            connection.execute(text("UPDATE jobs SET stage=:stage,progress_json=:progress,updated_at=:now WHERE id=:id"),
                               {'id': job['id'], 'stage': stage, 'progress': json.dumps(progress or {}), 'now': time.time()})

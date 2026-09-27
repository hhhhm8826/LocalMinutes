"""Queued generation and owner-controlled immutable draft versions."""
import hashlib
import json
import re
import time

from sqlalchemy import text

from .contracts import Minutes
from .minutes_identity import generation_identity
from .minutes_validation import text_payload, validate_minutes
from .repository import Conflict, Missing, identifier


def queue_generation(repository, settings, meeting_id, request, key):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', key):
        raise Conflict('INVALID_IDEMPOTENCY_KEY')
    if not request.allow_external_text:
        raise Conflict('EXTERNAL_TEXT_NOT_ALLOWED')
    digest = hashlib.sha256((meeting_id + request.model_dump_json()).encode()).hexdigest()
    with repository.write() as connection:
        alias = connection.execute(text('SELECT * FROM job_requests WHERE key=:key'), {'key': key}).mappings().first()
        if alias:
            if alias['request_hash'] != digest:
                raise Conflict('IDEMPOTENCY_CONFLICT')
            return dict(connection.execute(text('SELECT * FROM jobs WHERE id=:id'), {'id': alias['job_id']}).mappings().one())
        existing = connection.execute(text('SELECT * FROM jobs WHERE idempotency_key=:key'), {'key': key}).mappings().first()
        if existing:
            if existing['request_hash'] != digest or existing['meeting_id'] != meeting_id:
                raise Conflict('IDEMPOTENCY_CONFLICT')
            return dict(existing)
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL'),
                                     {'id': meeting_id}).mappings().first()
        if not meeting:
            raise Missing('MEETING_NOT_FOUND')
        if meeting['revision'] != request.expected_revision or meeting['transcript_version'] != request.transcript_version:
            raise Conflict('REVISION_CONFLICT')
        options = json.loads(meeting['settings_json'])
        options['allow_external_text'] = True
        identity = generation_identity(request.transcript_version, options, settings)
        candidates = connection.execute(text('''SELECT * FROM jobs WHERE meeting_id=:meeting
            AND json_extract(request_json,'$.generation_key')=:identity ORDER BY sequence DESC'''),
            {'meeting': meeting_id, 'identity': identity}).mappings().all()
        if not request.new_draft:
            for candidate in candidates:
                if candidate['state'] in {'QUEUED', 'RUNNING', 'BLOCKED', 'INTERRUPTED', 'COMPLETED'}:
                    connection.execute(text('INSERT INTO job_requests VALUES (:key,:hash,:job)'),
                                       {'key': key, 'hash': digest, 'job': candidate['id']})
                    return dict(candidate)
        active = connection.execute(text("SELECT 1 FROM jobs WHERE meeting_id=:id AND state IN ('QUEUED','RUNNING','CANCEL_REQUESTED','BLOCKED','INTERRUPTED')"),
                                    {'id': meeting_id}).first()
        if active:
            raise Conflict('MEETING_JOB_PENDING')
        number = connection.execute(text("SELECT COALESCE(MAX(json_extract(content_json,'$.revision')),0)+1 FROM minutes_revisions WHERE meeting_id=:id"),
                                    {'id': meeting_id}).scalar_one()
        snapshot = {'transcript_version': request.transcript_version, 'meeting': options,
                    'base_minutes_id': meeting['minutes_revision'], 'revision': number, 'generation_key': identity}
        job_id, now = identifier(), time.time()
        connection.execute(text('''INSERT INTO jobs(id,meeting_id,kind,state,stage,attempt_id,idempotency_key,
            request_hash,created_at,updated_at,transcript_version,request_json)
            VALUES (:id,:meeting,'summarize','QUEUED','SUMMARIZE',:attempt,:key,:hash,:now,:now,:version,:snapshot)'''),
            {'id': job_id, 'meeting': meeting_id, 'attempt': identifier(), 'key': key, 'hash': digest, 'now': now,
             'version': request.transcript_version, 'snapshot': json.dumps(snapshot, ensure_ascii=False)})
        if not json.loads(meeting['settings_json'])['allow_external_text']:
            connection.execute(text('UPDATE meetings SET settings_json=:settings,revision=revision+1,updated_at=:now WHERE id=:id'),
                               {'settings': json.dumps(options), 'now': now, 'id': meeting_id})
    return repository.job(job_id)


def finish_transcript_only(repository, job_id):
    with repository.write() as connection:
        job = connection.execute(text('SELECT * FROM jobs WHERE id=:id'), {'id': job_id}).mappings().first()
        if not job:
            raise Missing('JOB_NOT_FOUND')
        if job['state'] == 'COMPLETED_TRANSCRIPT_ONLY':
            return dict(job)
        if not job['transcript_version'] or job['state'] not in {'QUEUED', 'BLOCKED', 'FAILED', 'INTERRUPTED', 'CANCELLED'}:
            raise Conflict('TRANSCRIPT_ONLY_NOT_AVAILABLE')
        if job['pid'] is not None:
            raise Conflict('PROCESS_STOP_UNCONFIRMED')
        connection.execute(text("""UPDATE jobs SET state='COMPLETED_TRANSCRIPT_ONLY',finished_at=:now,
            updated_at=:now,blocked_reason=NULL,error_code=NULL,cancel_requested=0 WHERE id=:id"""),
            {'id': job_id, 'now': time.time()})
    return repository.job(job_id)


def read_minutes(repository, meeting_id, version=None):
    meeting = repository.meeting(meeting_id)
    version = version or meeting['minutes_revision']
    with repository.engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM minutes_revisions WHERE id=:id AND meeting_id=:meeting'),
                                 {'id': version, 'meeting': meeting_id}).mappings().first()
    if not row:
        raise Missing('MINUTES_NOT_READY')
    return {'id': row['id'], 'parent_id': row['parent_id'], 'content': json.loads(row['content_json']),
            'meeting_revision': meeting['revision'], 'stale_transcript': row['transcript_version'] != meeting['transcript_version']}


def list_minutes(repository, meeting_id):
    repository.meeting(meeting_id)
    with repository.engine.connect() as connection:
        return [dict(row) for row in connection.execute(text('''SELECT id,parent_id,transcript_version,created_at,
            json_extract(content_json,'$.revision') AS revision,json_extract(content_json,'$.status') AS status
            FROM minutes_revisions WHERE meeting_id=:id ORDER BY created_at DESC'''), {'id': meeting_id}).mappings()]


def edit_minutes(repository, meeting_id, parent_id, expected_revision, content=None, confirm=False):
    with repository.write() as connection:
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL'),
                                     {'id': meeting_id}).mappings().first()
        if not meeting:
            raise Missing('MEETING_NOT_FOUND')
        if meeting['revision'] != expected_revision or meeting['minutes_revision'] != parent_id:
            raise Conflict('REVISION_CONFLICT')
        row = connection.execute(text('SELECT * FROM minutes_revisions WHERE id=:id AND meeting_id=:meeting'),
                                 {'id': parent_id, 'meeting': meeting_id}).mappings().one()
        previous = Minutes.model_validate_json(row['content_json'])
        if previous.transcript_version != meeting['transcript_version']:
            raise Conflict('TRANSCRIPT_CHANGED')
        supplied = content or previous
        if (supplied.meeting_id != meeting_id or supplied.transcript_version != previous.transcript_version
                or supplied.revision != previous.revision):
            raise Conflict('MINUTES_SNAPSHOT_MISMATCH')
        candidate = supplied.model_copy(update={'status': 'confirmed' if confirm else 'draft'})
        transcript = json.loads(connection.execute(text('SELECT content_json FROM transcript_versions WHERE id=:id'),
                                                   {'id': previous.transcript_version}).scalar_one())
        options = json.loads(meeting['settings_json'])
        payload = text_payload(meeting_id, previous.transcript_version, previous.revision, options, transcript, require_consent=False)
        validated = validate_minutes(candidate.model_dump(), payload, options, generated=False)
        if confirm and any(item.review_status == 'needs_review' for item in [*validated.decisions, *validated.action_items]):
            raise Conflict('MINUTES_REVIEW_REQUIRED')
        number = connection.execute(text("SELECT COALESCE(MAX(json_extract(content_json,'$.revision')),0)+1 FROM minutes_revisions WHERE meeting_id=:id"),
                                    {'id': meeting_id}).scalar_one()
        version, now = identifier(), time.time()
        connection.execute(text('''INSERT INTO minutes_revisions VALUES (:id,:meeting,:transcript,:parent,:content,:now)'''),
            {'id': version, 'meeting': meeting_id, 'transcript': previous.transcript_version, 'parent': parent_id,
             'content': validated.model_copy(update={'revision': number}).model_dump_json(), 'now': now})
        connection.execute(text('UPDATE meetings SET minutes_revision=:version,revision=revision+1,updated_at=:now WHERE id=:id'),
                           {'id': meeting_id, 'version': version, 'now': now})
    return read_minutes(repository, meeting_id, version)

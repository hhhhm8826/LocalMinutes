"""Atomic URL registration and fenced publication into the shared media queue."""
import hashlib
import json
import re
import time

from sqlalchemy import text

from .contracts import MeetingCreate
from .repository import Conflict, identifier
from .youtube_policy import canonical_url


def register_youtube(repository, url, title, key, *, settings=None):
    from .ai_snapshot import capture
    from .settings import Settings
    settings = settings or Settings()
    url = canonical_url(url)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', key):
        raise Conflict('INVALID_IDEMPOTENCY_KEY')
    options = MeetingCreate(title=title.strip(), allow_external_text=True)
    digest = hashlib.sha256(json.dumps([url, options.model_dump(mode='json')], sort_keys=True).encode()).hexdigest()
    with repository.write() as connection:
        existing = connection.execute(text('SELECT * FROM jobs WHERE idempotency_key=:key'), {'key': key}).mappings().first()
        if existing:
            if existing['request_hash'] != digest:
                raise Conflict('IDEMPOTENCY_CONFLICT')
            return dict(existing)
        if connection.execute(text('SELECT 1 FROM job_requests WHERE key=:key'), {'key': key}).first():
            raise Conflict('IDEMPOTENCY_CONFLICT')
        meeting, job, attempt, now = identifier(), identifier(), identifier(), time.time()
        connection.execute(text("""INSERT INTO meetings(id,title,settings_json,document_kind,source_kind,
            source_metadata_json,created_at,updated_at) VALUES (:id,:title,:settings,'video_summary','youtube',:source,:now,:now)"""),
            {'id': meeting, 'title': options.title or 'YouTube 영상', 'settings': options.model_dump_json(),
             'source': json.dumps({'source_url': url}), 'now': now})
        connection.execute(text("""INSERT INTO jobs(id,meeting_id,kind,state,stage,attempt_id,idempotency_key,
            request_hash,created_at,updated_at,ai_config_json) VALUES (:id,:meeting,'transcribe','QUEUED','SOURCE_CHECK',:attempt,:key,:hash,:now,:now,:ai_config)"""),
            {'id': job, 'meeting': meeting, 'attempt': attempt, 'key': key, 'hash': digest, 'now': now,
             'ai_config': capture(connection, settings, {'document_kind': 'video_summary'}).model_dump_json()})
    return repository.job(job)

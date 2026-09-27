"""Durable call reservations and immutable minutes publication."""
import json
import time

from sqlalchemy import text

from .codex_provider import CodexFailure
from .repository import Conflict, identifier


def reserve_call(repository, job, maximum, fingerprint, *, purpose='initial', purpose_limit=None):
    with repository.write() as connection:
        repository.assert_current(connection, job)
        rows = connection.execute(text("SELECT metrics_json FROM usage_records WHERE job_id=:job AND stage='SUMMARIZE'"),
                                  {'job': job['id']}).scalars().all()
        reservations = sum(json.loads(value).get('call_reserved', False) for value in rows)
        if reservations >= maximum:
            raise CodexFailure('CODEX_JOB_CALL_BUDGET_EXHAUSTED')
        if purpose_limit is not None and sum(json.loads(value).get('purpose') == purpose for value in rows) >= purpose_limit:
            raise CodexFailure('CODEX_RETRY_BUDGET_EXHAUSTED')
        record_id = identifier()
        connection.execute(text('''INSERT INTO usage_records(id,job_id,attempt_id,stage,metrics_json,created_at)
            VALUES (:id,:job,:attempt,'SUMMARIZE',:metrics,:now)'''),
            {'id': record_id, 'job': job['id'], 'attempt': job['attempt_id'],
             'metrics': json.dumps({'call_reserved': True, 'status': 'RESERVED', 'fingerprint': fingerprint, 'purpose': purpose}), 'now': time.time()})
    return record_id


def finish_call(repository, record_id, status, metrics):
    # Reservation survives crashes and cancellation; updating metrics never refunds it.
    allowed = {'usage', 'item_types', 'error_codes', 'completed', 'wall_seconds', 'model', 'cli_version', 'stderr_sha256',
               'context_budget', 'prompt_bytes'}
    with repository.write() as connection:
        value = json.loads(connection.execute(text('SELECT metrics_json FROM usage_records WHERE id=:id'),
                                             {'id': record_id}).scalar_one())
        value.update({key: item for key, item in metrics.items() if key in allowed})
        value['status'] = status
        connection.execute(text('UPDATE usage_records SET metrics_json=:metrics WHERE id=:id'),
                           {'id': record_id, 'metrics': json.dumps(value)})


def save_minutes(repository, job, result, base_minutes_id):
    with repository.write() as connection:
        repository.assert_current(connection, job)
        current_job = connection.execute(text('SELECT * FROM jobs WHERE id=:id'), {'id': job['id']}).mappings().one()
        if current_job['minutes_result_id']:
            row = connection.execute(text('SELECT * FROM minutes_revisions WHERE id=:id'),
                                     {'id': current_job['minutes_result_id']}).mappings().one()
            return {'id': row['id'], 'content': json.loads(row['content_json'])}
        if result.meeting_id != job['meeting_id'] or result.transcript_version != current_job['transcript_version']:
            raise Conflict('MINUTES_SNAPSHOT_MISMATCH')
        if base_minutes_id and not connection.execute(text('SELECT 1 FROM minutes_revisions WHERE id=:id AND meeting_id=:meeting'),
            {'id': base_minutes_id, 'meeting': job['meeting_id']}).first():
            raise Conflict('MINUTES_PARENT_MISMATCH')
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id'),
                                     {'id': job['meeting_id']}).mappings().one()
        number = connection.execute(text("SELECT COALESCE(MAX(json_extract(content_json,'$.revision')),0)+1 FROM minutes_revisions WHERE meeting_id=:id"),
                                    {'id': job['meeting_id']}).scalar_one()
        value = result.model_copy(update={'revision': number})
        new_id, now = identifier(), time.time()
        connection.execute(text('''INSERT INTO minutes_revisions(id,meeting_id,transcript_version,parent_id,content_json,created_at)
            VALUES (:id,:meeting,:transcript,:parent,:content,:now)'''),
            {'id': new_id, 'meeting': job['meeting_id'], 'transcript': value.transcript_version,
             'parent': base_minutes_id, 'content': value.model_dump_json(), 'now': now})
        connection.execute(text('UPDATE jobs SET minutes_result_id=:result WHERE id=:id'),
                           {'id': job['id'], 'result': new_id})
        # Generated old snapshots remain available, but cannot replace newer owner edits.
        if (meeting['minutes_revision'] == base_minutes_id
                and meeting['transcript_version'] == value.transcript_version):
            connection.execute(text('''UPDATE meetings SET minutes_revision=:version,revision=revision+1,updated_at=:now
                WHERE id=:id'''), {'id': job['meeting_id'], 'version': new_id, 'now': now})
            options = json.loads(meeting['settings_json'])
            if not options.get('title') and hasattr(value, 'metadata'):
                options['title'] = value.metadata.title
                connection.execute(text('UPDATE meetings SET title=:title,settings_json=:options WHERE id=:id'),
                    {'id': job['meeting_id'], 'title': value.metadata.title, 'options': json.dumps(options, ensure_ascii=False)})
    return {'id': new_id, 'content': value.model_dump()}

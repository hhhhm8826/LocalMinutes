"""Consent-gated generation from an immutable transcript snapshot."""
import json

from sqlalchemy import text

from .codex_provider import CodexCliProvider, CodexFailure, INSTRUCTIONS
from .contracts import Minutes
from .minutes_storage import save_minutes
from .minutes_long import generate_minutes
from .minutes_context import FORMAT_VERSION, TASKS
from .minutes_validation import text_payload, validate_minutes
from .minutes_identity import generation_identity
from .speech_pipeline import checkpoint, fingerprint
from .temporary import attempt_prefix


def snapshot(repository, job, settings):
    with repository.write() as connection:
        repository.assert_current(connection, job)
        current = connection.execute(text('SELECT * FROM jobs WHERE id=:id'), {'id': job['id']}).mappings().one()
        if current['minutes_result_id']:
            return None
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id'),
                                     {'id': job['meeting_id']}).mappings().one()
        request = json.loads(current['request_json'])
        if not request:
            if not current['transcript_version']:
                raise CodexFailure('TRANSCRIPT_NOT_READY')
            number = connection.execute(text("SELECT COALESCE(MAX(json_extract(content_json,'$.revision')),0)+1 FROM minutes_revisions WHERE meeting_id=:id"),
                                        {'id': job['meeting_id']}).scalar_one()
            request = {'transcript_version': current['transcript_version'], 'meeting': json.loads(meeting['settings_json']),
                       'base_minutes_id': meeting['minutes_revision'], 'revision': number}
            request['generation_key'] = generation_identity(request['transcript_version'], request['meeting'], settings)
            connection.execute(text('UPDATE jobs SET request_json=:request WHERE id=:id'),
                               {'request': json.dumps(request), 'id': job['id']})
        if request.get('generation_key') != generation_identity(request['transcript_version'], request['meeting'], settings):
            raise CodexFailure('CODEX_GENERATION_CONFIG_CHANGED')
        transcript = connection.execute(text('SELECT content_json FROM transcript_versions WHERE id=:id AND meeting_id=:meeting'),
            {'id': request['transcript_version'], 'meeting': job['meeting_id']}).scalar_one()
    payload = text_payload(job['meeting_id'], request['transcript_version'], request['revision'], request['meeting'], json.loads(transcript))
    return request, payload


def run_minutes(repository, settings, job):
    try:
        material = snapshot(repository, job, settings)
        if material is None:
            return 'COMPLETED', None
        request, payload = material
        inputs = {'payload': fingerprint(payload), 'model': settings.codex_model, 'prompt': fingerprint(INSTRUCTIONS),
                  'schema': fingerprint(Minutes.model_json_schema()), 'adapter': FORMAT_VERSION, 'tasks': fingerprint(TASKS),
                  'generation_key': request['generation_key']}
        provider = CodexCliProvider(settings)
        provider.temporary_prefix = attempt_prefix(job['id'], job['attempt_id'])
        def calculate():
            return generate_minutes(repository, settings, job, provider, payload, request['meeting'])
        generated = checkpoint(repository, settings, job, 'SUMMARIZE', inputs, calculate)
        result = validate_minutes(generated, payload, request['meeting'])
        repository.stage(job, 'SAVE')
        save_minutes(repository, job, result, request['base_minutes_id'])
        return 'COMPLETED', None
    except CodexFailure as exc:
        blocked = {'CODEX_LOGIN_REQUIRED', 'CODEX_USAGE_LIMIT', 'CODEX_MODEL_UNAVAILABLE', 'CODEX_INSTALL_REQUIRED',
                   'CODEX_VERSION_UNVERIFIED', 'CODEX_ISOLATION_UNVERIFIED', 'CODEX_INPUT_REQUIRES_CHUNKING',
                   'CODEX_JOB_CALL_BUDGET_EXHAUSTED', 'EXTERNAL_TEXT_NOT_ALLOWED', 'CODEX_MODEL_METADATA_REQUIRED',
                   'CODEX_CONTEXT_BUDGET_TOO_SMALL', 'CODEX_JOB_CALL_BUDGET_REQUIRED',
                   'CODEX_INTEGRATION_BUDGET_REQUIRED', 'CODEX_SEGMENT_EXCEEDS_CONTEXT', 'CODEX_GENERATION_CONFIG_CHANGED'}
        return ('BLOCKED' if exc.code in blocked else 'FAILED'), exc.code

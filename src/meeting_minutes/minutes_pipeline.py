"""Consent-gated generation from an immutable transcript snapshot."""
import json

from sqlalchemy import text

from .ai_common import AIFailure, INSTRUCTIONS
from .ai_runtime import GenerationRuntime, create_provider
from .minutes_storage import save_minutes, actual_model_for_job
from .minutes_long import generate_minutes
from .minutes_context import FORMAT_VERSION, TASKS
from .minutes_generation import generation_payload, materialize_generation, generation_model
from .minutes_identity import generation_identity
from .ai_snapshot import parse as parse_ai_config
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
        raw_config = current['ai_config_json']
        ai_config = parse_ai_config(raw_config, meeting)
        if not request:
            if not current['transcript_version']:
                raise AIFailure('TRANSCRIPT_NOT_READY')
            number = connection.execute(text("SELECT COALESCE(MAX(json_extract(content_json,'$.revision')),0)+1 FROM minutes_revisions WHERE meeting_id=:id"),
                                        {'id': job['meeting_id']}).scalar_one()
            request = {'transcript_version': current['transcript_version'], 'meeting': json.loads(meeting['settings_json']),
                       'base_minutes_id': meeting['minutes_revision'], 'revision': number}
            request['meeting'].update(document_kind=meeting['document_kind'], source_kind=meeting['source_kind'],
                                      source_metadata=json.loads(meeting['source_metadata_json']))
            request['generation_key'] = generation_identity(request['transcript_version'], request['meeting'], settings, ai_config)
            connection.execute(text('UPDATE jobs SET request_json=:request WHERE id=:id'),
                               {'request': json.dumps(request), 'id': job['id']})
        if request.get('generation_key') != generation_identity(request['transcript_version'], request['meeting'], settings, ai_config):
            raise AIFailure('CODEX_GENERATION_CONFIG_CHANGED')
        transcript = connection.execute(text('SELECT content_json FROM transcript_versions WHERE id=:id AND meeting_id=:meeting'),
            {'id': request['transcript_version'], 'meeting': job['meeting_id']}).scalar_one()
    payload = generation_payload(job['meeting_id'], request['transcript_version'], request['revision'], request['meeting'], json.loads(transcript))
    return request, payload, ai_config


def run_minutes(repository, settings, job):
    try:
        material = snapshot(repository, job, settings)
        if material is None:
            return 'COMPLETED', None
        request, payload, ai_config = material
        runtime = GenerationRuntime(settings, ai_config)
        inputs = {'ai_config': ai_config.model_dump(mode='json'), 'payload': fingerprint(payload), 'model': runtime.model, 'prompt': fingerprint(INSTRUCTIONS),
                  'schema': fingerprint(generation_model(payload).model_json_schema()), 'adapter': FORMAT_VERSION, 'tasks': fingerprint(TASKS),
                  'generation_key': request['generation_key']}
        provider = create_provider(runtime)
        provider.temporary_prefix = attempt_prefix(job['id'], job['attempt_id'])
        def calculate():
            return generate_minutes(repository, settings, job, provider, payload, request['meeting'], runtime=runtime)
        generated = checkpoint(repository, settings, job, 'SUMMARIZE', inputs, calculate)
        result = materialize_generation(generated, payload, request['meeting'])
        result.metadata = result.metadata.model_copy(update={'ai_provider': ai_config.provider,
            'ai_configured_model': ai_config.model, 'ai_policy_revision': ai_config.policy_revision,
            'ai_actual_model': actual_model_for_job(repository, job, ai_config)})
        repository.stage(job, 'SAVE')
        save_minutes(repository, job, result, request['base_minutes_id'])
        return 'COMPLETED', None
    except AIFailure as exc:
        blocked = {'AI_DAILY_REQUEST_BUDGET_EXHAUSTED', 'AI_REQUEST_RATE_WAIT', 'AI_WEEKLY_BUDGET_EXHAUSTED', 'AI_LEGACY_CONFIG_REQUIRED', 'AI_CONFIG_INVALID', 'AI_GENERATION_CONFIG_CHANGED', 'AI_ADAPTER_NOT_READY', 'AI_INSTALL_REQUIRED', 'AI_SUBSCRIPTION_REQUIRED', 'AI_CLI_VERSION_UNVERIFIED',
                   'AI_ISOLATION_UNVERIFIED', 'CLAUDE_RUNTIME_HOME_REQUIRED', 'AI_AUTH_REQUIRED', 'AI_AUTH_INVALID', 'AI_RATE_LIMIT', 'AI_MODEL_UNAVAILABLE',
                   'AI_CREDENTIALS_CHANGED', 'AI_MODEL_METADATA_REQUIRED', 'AI_CONTEXT_BUDGET_TOO_SMALL', 'AI_PROVIDER_BUSY', 'AI_LOCK_UNSAFE',
                   'CODEX_LOGIN_REQUIRED', 'CODEX_USAGE_LIMIT', 'CODEX_MODEL_UNAVAILABLE', 'CODEX_INSTALL_REQUIRED',
                   'CODEX_VERSION_UNVERIFIED', 'CODEX_ISOLATION_UNVERIFIED', 'CODEX_INPUT_REQUIRES_CHUNKING',
                   'CODEX_JOB_CALL_BUDGET_EXHAUSTED', 'EXTERNAL_TEXT_NOT_ALLOWED', 'CODEX_MODEL_METADATA_REQUIRED',
                   'CODEX_CONTEXT_BUDGET_TOO_SMALL', 'CODEX_JOB_CALL_BUDGET_REQUIRED',
                   'CODEX_INTEGRATION_BUDGET_REQUIRED', 'CODEX_SEGMENT_EXCEEDS_CONTEXT', 'CODEX_GENERATION_CONFIG_CHANGED'}
        return ('BLOCKED' if exc.code in blocked else 'FAILED'), exc.code

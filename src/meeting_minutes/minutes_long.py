"""Evidence-bearing candidate extraction followed by chronological integration."""
import json
import time
from typing import Literal

from pydantic import Field, ValidationError
from sqlalchemy import text

from .codex_provider import CodexFailure, INSTRUCTIONS, strict_schema
from .contracts import Contract, Minutes
from .minutes_generation import generation_model, validate_generation
from .minutes_calls import call_with_retries
from .minutes_context import FORMAT_VERSION, TASKS, context_budget, render_prompt, split_payload
from .minutes_validation import validate_minutes
from .repository import identifier
from .speech_pipeline import cached_checkpoint, checkpoint, fingerprint


class Candidate(Contract):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal['topic', 'proposal', 'decision', 'reversal', 'resolution', 'action', 'open_question', 'number', 'claim', 'opinion', 'prediction']
    text: str = Field(min_length=1, max_length=6000)
    source_segment_ids: list[str] = Field(min_length=1, max_length=100)
    owner_speaker_id: str | None = None
    due_date: str | None = None
    due_date_original_expression: str | None = None


class CandidateBatch(Contract):
    chunk_index: int = Field(ge=0)
    items: list[Candidate] = Field(max_length=200)


def validate_candidates(value, payload, meeting):
    try:
        result = CandidateBatch.model_validate(value)
    except ValidationError as exc:
        raise CodexFailure('MINUTES_SCHEMA_INVALID') from exc
    if result.chunk_index != payload['chunk_index']:
        raise CodexFailure('MINUTES_SNAPSHOT_MISMATCH')
    # Reuse the same evidence, owner and date checks as final minutes without inventing prose summaries.
    wrapper = Minutes(meeting_id=payload['meeting_id'], transcript_version=payload['transcript_version'],
        revision=payload['revision'], summary='후보 근거 검증', topics=[], decisions=[], open_questions=[], review_notes=[],
        action_items=[{'id': item.id, 'task': item.text, 'source_segment_ids': item.source_segment_ids,
                       'owner_speaker_id': item.owner_speaker_id, 'due_date': item.due_date,
                       'due_date_original_expression': item.due_date_original_expression} for item in result.items])
    validate_minutes(wrapper.model_dump(), payload, {key: value for key, value in meeting.items()
                     if key not in {'document_kind', 'source_kind', 'source_metadata'}})
    return result.model_dump()


def call_inputs(payload, schema, settings):
    return {'payload': fingerprint(payload), 'schema': fingerprint(schema), 'model': settings.codex_model,
            'prompt': fingerprint([INSTRUCTIONS, TASKS]), 'format_version': FORMAT_VERSION}


def remaining_calls(repository, job, maximum):
    with repository.engine.connect() as connection:
        rows = connection.execute(text("SELECT metrics_json FROM usage_records WHERE job_id=:id AND stage='SUMMARIZE'"),
                                  {'id': job['id']}).scalars().all()
    return maximum - sum(json.loads(row).get('call_reserved', False) for row in rows)


def generate_minutes(repository, settings, job, provider, payload, meeting):
    schema = generation_model(payload).model_json_schema()
    budget = context_budget(settings, strict_schema(schema))
    full_size = len(render_prompt(payload).encode())
    if full_size <= budget['max_prompt_bytes']:
        inputs = call_inputs(payload, schema, settings)
        return call_with_retries(repository, settings, job, provider, payload, schema,
            lambda value: validate_generation(value, payload, meeting).model_dump(), fingerprint(inputs))
    candidate_schema = CandidateBatch.model_json_schema()
    candidate_budget = context_budget(settings, strict_schema(candidate_schema))
    chunks = split_payload(payload, candidate_budget)
    chunk_inputs = [call_inputs(chunk, candidate_schema, settings) for chunk in chunks]
    missing = sum(not cached_checkpoint(repository, settings, job, 'SUMMARIZE', inputs)[0] for inputs in chunk_inputs)
    if missing and missing + 1 > remaining_calls(repository, job, settings.codex_max_calls):
        raise CodexFailure('CODEX_JOB_CALL_BUDGET_REQUIRED')
    with repository.write() as connection:
        repository.assert_current(connection, job)
        connection.execute(text("INSERT INTO usage_records VALUES (:id,:job,:attempt,'SUMMARIZE',:metrics,:now)"),
            {'id': identifier(), 'job': job['id'], 'attempt': job['attempt_id'], 'now': time.time(),
             'metrics': json.dumps({'planning': {'mode': 'extract_integrate', 'chunks': len(chunks),
                                    'all_segment_count': len(payload['segments']), 'source_prompt_bytes': full_size,
                                    'candidate_budget': candidate_budget, 'final_budget': budget}})})
    batches = []
    for chunk, inputs in zip(chunks, chunk_inputs, strict=True):
        repository.stage(job, 'SUMMARIZE', {'chunk': chunk['chunk_index'] + 1, 'chunks': len(chunks)})
        def extract(chunk=chunk, inputs=inputs):
            return call_with_retries(repository, settings, job, provider, chunk, candidate_schema,
                lambda value: validate_candidates(value, chunk, meeting), fingerprint(inputs))
        batches.append(checkpoint(repository, settings, job, 'SUMMARIZE', inputs, extract))
    referenced = {source for batch in batches for item in batch['items'] for source in item['source_segment_ids']}
    integration = dict(payload, generation_mode='integrate', evidence_layout='references', candidate_batches=batches,
                       segments=[segment for segment in payload['segments'] if segment['id'] in referenced])
    if len(render_prompt(integration).encode()) > budget['max_prompt_bytes']:
        raise CodexFailure('CODEX_INTEGRATION_BUDGET_REQUIRED')
    inputs = call_inputs(integration, schema, settings)
    if not cached_checkpoint(repository, settings, job, 'SUMMARIZE', inputs)[0] and remaining_calls(repository, job, settings.codex_max_calls) < 1:
        raise CodexFailure('CODEX_JOB_CALL_BUDGET_REQUIRED')
    def integrate():
        return call_with_retries(repository, settings, job, provider, integration, schema,
            lambda value: validate_generation(value, payload, meeting).model_dump(), fingerprint(inputs))
    return checkpoint(repository, settings, job, 'SUMMARIZE', inputs, integrate)

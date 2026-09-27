import json

import pytest
from sqlalchemy import text

from meeting_minutes.codex_provider import CodexFailure, strict_schema
from meeting_minutes.contracts import Minutes
from meeting_minutes.documents import GeneratedMeeting
from meeting_minutes.minutes_context import compact, context_budget, render_prompt, split_payload
from meeting_minutes.minutes_long import CandidateBatch, generate_minutes
from meeting_minutes.minutes_validation import text_payload
from test_queue_media import context, register  # noqa: F401


def material(count=60):
    meeting = {'title': '긴 가상 회의', 'allow_external_text': True, 'language': 'ko'}
    transcript = {'speakers': {'A': {'name': '가상 화자'}}, 'segments': [
        {'id': f's{i:04d}', 'speaker_id': 'A', 'start_ms': i * 1000, 'end_ms': i * 1000 + 900,
         'text': ('검토 자료를 확인했습니다. ' * 8) + str(i)} for i in range(count)]}
    transcript['segments'][0]['text'] = '금요일 배포에 합의했습니다.'
    transcript['segments'][-1]['text'] = '앞선 금요일 배포 결정을 취소합니다. 점검만 합니다.'
    return meeting, text_payload('m', 'v', 1, meeting, transcript)


def test_compact_and_chunking_preserve_every_source_in_order(context):  # noqa: F811
    settings, _ = context
    settings.codex_input_bytes = 5000
    _, payload = material()
    budget = context_budget(settings, strict_schema(CandidateBatch.model_json_schema()))
    chunks = split_payload(payload, budget)
    assert len(chunks) > 1
    flattened = [segment for chunk in chunks for segment in chunk['segments']]
    assert flattened == payload['segments']
    assert all(len(render_prompt(chunk).encode()) <= budget['max_prompt_bytes'] for chunk in chunks)
    assert [row[2] for row in compact(payload)['segments']] == [segment['text'] for segment in payload['segments']]
    assert budget['effective_context_tokens'] == 258400


def test_long_pipeline_keeps_reversal_evidence_and_reuses_finished_chunks(context):  # noqa: F811
    settings, repo = context
    settings.codex_input_bytes = 5000
    register(context)
    job = repo.claim()
    meeting, payload = material(35)
    calls = []
    class Provider:
        def generate(self, value, schema, reserve):
            reserve()
            calls.append(value)
            if value.get('generation_mode') == 'extract':
                selected = [segment for segment in value['segments'] if '배포' in segment['text']]
                return {'chunk_index': value['chunk_index'], 'items': [
                    {'id': segment['id'], 'kind': 'reversal' if '취소' in segment['text'] else 'decision',
                     'text': segment['text'], 'source_segment_ids': [segment['id']]} for segment in selected]}, {}
            assert value['generation_mode'] == 'integrate'
            packed = compact(value)
            assert packed['evidence_layout'] == 'references'
            assert all(len(row) == 3 for row in packed['segments'])
            ids = [segment['id'] for segment in value['segments']]
            assert ids == ['s0000', 's0034']
            assert [item['kind'] for batch in value['candidate_batches'] for item in batch['items']] == ['decision', 'reversal']
            return GeneratedMeeting(meeting_id='m', transcript_version='v', revision=1, summary='배포를 취소하고 점검만 한다.', topics=[],
                decisions=[{'id': 'd1', 'text': '배포 취소', 'source_segment_ids': ['s0034']}],
                action_items=[], open_questions=[], review_notes=[]).model_dump(), {}
    result = generate_minutes(repo, settings, job, Provider(), payload, meeting)
    assert result['decisions'][0]['source_segment_ids'] == ['s0034']
    first_count = len(calls)
    assert first_count > 2
    assert generate_minutes(repo, settings, job, Provider(), payload, meeting) == result
    assert len(calls) == first_count
    with repo.engine.connect() as connection:
        paths = connection.execute(text('SELECT path FROM stage_artifacts')).scalars().all()
    assert len(set(paths)) == len(paths)


def test_insufficient_total_call_budget_stops_before_any_generation(context):  # noqa: F811
    settings, repo = context
    settings.codex_input_bytes, settings.codex_max_calls = 2000, 2
    register(context)
    job = repo.claim()
    meeting, payload = material()
    class Provider:
        def generate(self, *args):
            raise AssertionError('no call can start')
    with pytest.raises(CodexFailure, match='CALL_BUDGET_REQUIRED'):
        generate_minutes(repo, settings, job, Provider(), payload, meeting)
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM usage_records')).scalar_one() == 0


def test_missing_model_limit_fails_without_guessing(context):  # noqa: F811
    settings, _ = context
    (settings.codex_home / 'models_cache.json').write_text(json.dumps({'models': []}))
    with pytest.raises(CodexFailure, match='MODEL_METADATA_REQUIRED'):
        context_budget(settings, Minutes.model_json_schema())

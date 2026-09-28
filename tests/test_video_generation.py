import copy
import json

import pytest
from sqlalchemy import text

from meeting_minutes.codex_provider import CodexFailure
from meeting_minutes.documents import GeneratedVideo
from meeting_minutes.minutes_context import render_prompt
from meeting_minutes.minutes_generation import generation_payload, materialize_generation
from meeting_minutes.minutes_identity import generation_identity
from meeting_minutes.settings import Settings
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.minutes_management import queue_generation, read_minutes, edit_minutes
from meeting_minutes.minutes_pipeline import run_minutes
from meeting_minutes.repository import Conflict
from meeting_minutes.library import export_minutes
from test_minutes_management import ready
from test_queue_media import context  # noqa: F401
from test_queue_media import register
from test_minutes_long import material
from meeting_minutes.minutes_long import generate_minutes


def fixture():
    meeting = {'title': '경제 강연', 'allow_external_text': True, 'document_kind': 'video_summary',
               'source_kind': 'youtube', 'occurred_at': '2026-09-27T12:00:00+09:00',
               'source_metadata': {'source_url': 'https://www.youtube.com/watch?v=AbCde_123-4',
                                   'channel': '강연자', 'published_at': None}}
    transcript = {'speakers': {'A': {'name': '화자 1'}}, 'segments': [
        {'id': 's1', 'start_ms': 3601000, 'end_ms': 3609000, 'speaker_id': 'A',
         'text': '작년 매출은 30억 원입니다. 내년에는 증가할 것이라는 제 예상입니다.'}]}
    payload = generation_payload('m', 'v', 1, meeting, transcript)
    source = payload['segments'][0]['id']
    generated = GeneratedVideo(meeting_id='m', transcript_version='v', revision=1,
        summary='강연자는 매출과 향후 전망을 설명했다.',
        topics=[{'id': 't', 'title': '매출 전망', 'text': '작년 매출 30억 원과 내년 증가 전망을 설명한다.',
                 'ranges': [{'first': source, 'last': source}]}],
        claims=[{'text': '작년 매출 30억 원', 'attribution': '강연자', 'kind': 'number', 'source_segment_ids': [source]}]).model_dump()
    return meeting, payload, generated


def test_video_document_is_independent_readonly_shape_with_server_times():
    meeting, payload, generated = fixture()
    assert payload['meeting']['occurred_at'] is None
    result = materialize_generation(generated, payload, meeting)
    assert result.document_kind == 'video_summary'
    assert result.topics[0].starts[0].start_ms == 3601000
    assert result.metadata.published_at is None and result.metadata.occurred_at is None
    assert result.metadata.generated_at is not None
    assert result.metadata.channel == '강연자'
    assert not {'decisions', 'action_items', 'status', 'review_notes'} & result.model_dump().keys()
    assert result.claims[0].attribution == '강연자'
    for mode in ('minutes', 'extract', 'integrate'):
        assert 'video' in render_prompt(dict(payload, generation_mode=mode)).split('\n')[0].lower()


@pytest.mark.parametrize('mutation', ['source', 'snapshot', 'attribution', 'extra'])
def test_video_rejects_forged_evidence_snapshot_and_meeting_output(mutation):
    meeting, payload, generated = fixture()
    value = copy.deepcopy(generated)
    if mutation == 'source':
        value['claims'][0]['source_segment_ids'] = ['invented']
    elif mutation == 'snapshot':
        value['transcript_version'] = 'other'
    elif mutation == 'attribution':
        value['claims'][0]['attribution'] = ''
    else:
        value['decisions'] = []
    with pytest.raises(CodexFailure):
        materialize_generation(value, payload, meeting)


def test_video_identity_separates_kind_and_source_metadata():
    meeting, _, _ = fixture()
    settings = Settings()
    identity = generation_identity('v', meeting, settings)
    assert identity != generation_identity('v', dict(meeting, document_kind='meeting'), settings)
    assert identity != generation_identity('v', dict(meeting, source_metadata={'published_at': '2020-01-01'}), settings)


def test_video_queue_snapshot_publishes_summary_and_rejects_edit_confirm(context, monkeypatch):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    with repo.write() as connection:
        connection.execute(text("UPDATE meetings SET document_kind='video_summary',source_kind='youtube',source_metadata_json=:source WHERE id=:id"),
            {'id': meeting['id'], 'source': json.dumps({'channel': '강연자', 'published_at': None})})
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'video-generation-fixture')
    job = repo.claim()
    assert job['id'] == queued['id']
    calls = []
    class Provider:
        def __init__(self, settings):
            pass
        def generate(self, payload, schema, reserve):
            reserve()
            calls.append(payload)
            assert payload['document_kind'] == 'video_summary'
            assert 'claims' in schema['properties'] and 'decisions' not in schema['properties']
            return GeneratedVideo(meeting_id=meeting['id'], transcript_version=version, revision=payload['revision'],
                summary='영상에서 논의한 내용을 요약한다.', topics=[], claims=[]).model_dump(), {}
    monkeypatch.setattr('meeting_minutes.ai_runtime.CodexCliProvider', Provider)
    assert run_minutes(repo, settings, job) == ('COMPLETED', None)
    assert run_minutes(repo, settings, job) == ('COMPLETED', None)
    assert len(calls) == 1
    document = read_minutes(repo, meeting['id'])
    assert document['document']['document_kind'] == 'video_summary'
    assert document['document']['metadata']['channel'] == '강연자'
    with pytest.raises(Conflict):
        edit_minutes(repo, meeting['id'], document['id'], document['meeting_revision'], confirm=True)
    exported = export_minutes(repo, meeting['id'])
    assert '영상에서 논의한 내용을 요약한다' in exported and '## 요약' in exported
    assert '버전 1' not in exported


def test_long_video_extract_integrate_keeps_kind_and_reuses_checkpoints(context):  # noqa: F811
    settings, repo = context
    settings.codex_input_bytes = 5000
    register(context)
    job = repo.claim()
    meeting, payload = material(35)
    meeting['document_kind'] = payload['document_kind'] = 'video_summary'
    calls = []
    class Provider:
        def generate(self, value, schema, reserve):
            reserve()
            calls.append(value)
            assert value['document_kind'] == 'video_summary'
            if value.get('generation_mode') == 'extract':
                selected = value['segments'][0]
                return {'chunk_index': value['chunk_index'], 'items': [{'id': selected['id'], 'kind': 'claim',
                    'text': '발표자가 자료를 설명했다.', 'source_segment_ids': [selected['id']]}]}, {}
            assert value['generation_mode'] == 'integrate'
            assert 'claims' in schema['properties'] and 'action_items' not in schema['properties']
            return GeneratedVideo(meeting_id='m', transcript_version='v', revision=1,
                summary='발표자가 자료를 설명했다.', topics=[], claims=[]).model_dump(), {}
    result = generate_minutes(repo, settings, job, Provider(), payload, meeting)
    count = len(calls)
    assert count > 2 and result['document_kind'] == 'video_summary'
    assert generate_minutes(repo, settings, job, Provider(), payload, meeting) == result
    assert len(calls) == count

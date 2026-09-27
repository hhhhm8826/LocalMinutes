import copy

import pytest
from sqlalchemy import text

from meeting_minutes.codex_provider import CodexFailure
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.documents import GeneratedMeeting, ManualAction, MeetingDocument
from meeting_minutes.minutes_generation import generation_payload, materialize_generation, validate_generation
from meeting_minutes.minutes_management import edit_minutes, queue_generation, read_minutes
from meeting_minutes.minutes_storage import save_minutes
from meeting_minutes.library import export_minutes
from meeting_minutes.repository import Conflict
from test_minutes_management import complete, ready
from test_queue_media import context  # noqa: F401


def test_generated_ranges_materialize_server_times_without_raw_snapshot():
    options = {'title': '교정된 회의', 'allow_external_text': True}
    transcript = {'speakers': {'A': {'name': '화자 1'}}, 'diarization': {'embeddings': 'PRIVATE'},
                  'segments': [{'id': f's{i}', 'start_ms': i * 2000, 'end_ms': i * 2000 + 1000,
                                'text': sentence, 'speaker_id': 'A'} for i, sentence in enumerate([
                      '금요일 배포를 제안합니다.', '그럼 다 퇴사할까요? 농담입니다.', '배포는 보안 확인 뒤로 보류합니다.'])]}
    payload = generation_payload('m', 'v', 1, options, transcript)
    assert 'PRIVATE' not in str(payload)
    ids = [s['id'] for s in payload['segments']]
    value = GeneratedMeeting(meeting_id='m', transcript_version='v', revision=1, summary='보안 확인 후 배포 여부를 정한다.',
        topics=[{'id': 't', 'title': '배포 검토', 'text': '배포 제안 후 보안 확인까지 보류했다.',
                 'conclusion': '보류', 'conclusion_kind': 'deferred',
                 'ranges': [{'first': ids[0], 'last': ids[0]}, {'first': ids[2], 'last': ids[2]}]}],
        decisions=[], action_items=[], open_questions=[], review_notes=[]).model_dump()
    result = materialize_generation(value, payload, options)
    assert [start.start_ms for start in result.topics[0].starts] == [0]
    assert result.metadata.title == options['title']
    assert 'embeddings' not in result.model_dump_json()
    bad = copy.deepcopy(value)
    bad['topics'][0]['ranges'][0]['first'] = 'invented'
    with pytest.raises(CodexFailure):
        validate_generation(bad, payload, options)
    wide = copy.deepcopy(payload)
    wide['segments'] = [dict(payload['segments'][0], id=f'u{i}', start_ms=i * 1000,
                             end_ms=i * 1000 + 500) for i in range(150)]
    extended = copy.deepcopy(value)
    extended['topics'][0]['ranges'] = [{'first': 'u0', 'last': 'u149'}]
    assert len(materialize_generation(extended, wide, options).topics[0].starts[0].utterance_ids) == 150
    # Mock structure only: semantic quality needs the separately budgeted live evaluation.


def test_expired_source_still_allows_manual_edit_confirm_and_export(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'expiry-edit-fixture')
    original = complete(repo, job, version)
    with repo.write() as connection:
        connection.execute(text('DELETE FROM transcript_versions WHERE meeting_id=:id'), {'id': meeting['id']})
        connection.execute(text('UPDATE meetings SET transcript_version=NULL WHERE id=:id'), {'id': meeting['id']})
    current = read_minutes(repo, meeting['id'])
    edited = MeetingDocument.model_validate(current['document'])
    edited.action_items.append(ManualAction(id='manual', task='내부 확인', owner_name='운영팀', owner_speaker_id=None,
                                          due_date=None, due_date_original_expression=None, source_segment_ids=[]))
    result = edit_minutes(repo, meeting['id'], original['id'], current['meeting_revision'], edited)
    assert result['content']['action_items'][0]['review_status'] == 'user_authored'
    with pytest.raises(Conflict, match='REVISION_CONFLICT'):
        edit_minutes(repo, meeting['id'], original['id'], current['meeting_revision'], edited)
    confirmed = edit_minutes(repo, meeting['id'], result['id'], result['meeting_revision'], confirm=True)
    assert confirmed['content']['status'] == 'confirmed'
    assert '운영팀' in export_minutes(repo, meeting['id'])
    assert not confirmed['source_available']


def test_regeneration_promotes_new_result_and_preserves_user_document(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'protect-first-fixture')
    original = complete(repo, job, version)
    record = read_minutes(repo, meeting['id'])
    edited = MeetingDocument.model_validate(record['document'])
    edited.summary = '사용자가 정리한 내용'
    saved = edit_minutes(repo, meeting['id'], original['id'], record['meeting_revision'], edited)
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=saved['meeting_revision'],
        transcript_version=version, allow_external_text=True, new_draft=True), 'protect-second-fixture')
    active = repo.claim()
    assert active['id'] == queued['id']
    generated = edited.model_copy(update={'summary': '새 자동 초안', 'user_edited': False})
    fresh = save_minutes(repo, active, generated, saved['id'])
    assert fresh['id'] != saved['id']
    assert repo.meeting(meeting['id'])['minutes_revision'] == fresh['id']
    assert read_minutes(repo, meeting['id'], saved['id'])['content']['summary'] == '사용자가 정리한 내용'
    assert read_minutes(repo, meeting['id'], fresh['id'])['content']['summary'] == '새 자동 초안'


def test_generated_document_title_respects_user_and_youtube():
    transcript = {'speakers':{}, 'segments':[]}
    value = GeneratedMeeting(meeting_id='m', transcript_version='v', revision=1, title='분기 채용 계획',
        summary='채용을 논의했다.',topics=[],decisions=[],action_items=[],open_questions=[],review_notes=[]).model_dump()
    for options, expected in [({'title':''}, '분기 채용 계획'), ({'title':'직접 정한 제목'}, '직접 정한 제목'),
        ({'title':'','source_kind':'youtube','source_metadata':{'title':'원본 영상 제목'}}, '원본 영상 제목')]:
        options['allow_external_text'] = True
        payload = generation_payload('m','v',1,options,transcript)
        assert materialize_generation(value,payload,options).metadata.title == expected


def test_regeneration_does_not_replace_edits_saved_during_generation(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'race-first-fixture')
    original = complete(repo, job, version)
    record = read_minutes(repo, meeting['id'])
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=record['meeting_revision'],
        transcript_version=version, allow_external_text=True,new_draft=True),'race-second-fixture')
    active = repo.claim('summary')
    edited = MeetingDocument.model_validate(record['document'])
    edited.summary = '생성 도중 별도 수정'
    saved = edit_minutes(repo,meeting['id'],original['id'],record['meeting_revision'],edited)
    fresh = save_minutes(repo,active,edited.model_copy(update={'summary':'동시 생성본'}),original['id'])
    assert repo.meeting(meeting['id'])['minutes_revision'] == saved['id']
    assert read_minutes(repo,meeting['id'],fresh['id'])['content']['summary'] == '동시 생성본'

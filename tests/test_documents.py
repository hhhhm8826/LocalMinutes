import copy
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from alembic import command
from alembic.config import Config
from pathlib import Path

from meeting_minutes.documents import (GeneratedTopic, MeetingDocument, VideoDocument,
                                       legacy_document, resolve_starts)
from meeting_minutes.contracts import MeetingCreate
from meeting_minutes.minutes_management import read_minutes
from meeting_minutes.repository import Repository
from meeting_minutes.storage import make_engine, migrate
from test_queue_media import context  # noqa: F401


def old_minutes(meeting_id='m'):
    return {'schema_version': 1, 'meeting_id': meeting_id, 'transcript_version': 'v', 'revision': 1,
            'summary': '기존 사용자 문서', 'topics': [{'id': 't', 'text': '기존 논제', 'source_segment_ids': ['s1', 's3']}],
            'decisions': [], 'action_items': [], 'open_questions': [], 'review_notes': []}


def test_range_union_preserves_disjoint_recurrence_and_rejects_fabrication():
    reading = {'utterances': [{'id': f'u{i}', 'start_ms': i * 1000, 'end_ms': i * 1000 + 800} for i in range(8)]}
    starts = resolve_starts([{'first': 'u6', 'last': 'u7'}, {'first': 'u1', 'last': 'u2'},
                             {'first': 'u2', 'last': 'u3'}, {'first': 'u1', 'last': 'u1'}], reading)
    assert [s.start_ms for s in starts] == [1000, 6000]
    assert [s.utterance_ids for s in starts] == [['u1', 'u2', 'u3'], ['u6', 'u7']]
    with pytest.raises(ValueError, match='UNAVAILABLE'):
        resolve_starts([{'first': 'invented', 'last': 'u3'}], reading)
    with pytest.raises(ValueError, match='REVERSED'):
        resolve_starts([{'first': 'u3', 'last': 'u1'}], reading)
    with pytest.raises(ValidationError):
        GeneratedTopic(id='x', title='논제', text='내용', conclusion='', conclusion_kind='discussion',
                       ranges=[{'first': 'u1', 'last': 'u1'}], start_ms=999)


def test_legacy_adapter_preserves_content_with_or_without_source():
    value = old_minutes()
    original = copy.deepcopy(value)
    transcript = {'segments': [{'id': f's{i}', 'start_ms': i * 1000, 'end_ms': i * 1000 + 600,
                               'text': f'문장 {i}.', 'speaker_id': 'A'} for i in range(1, 4)]}
    available = legacy_document(value, {'title': '회의'}, transcript)
    expired = legacy_document(value, {'title': '회의'})
    assert available.summary == expired.summary == value['summary']
    assert [s.start_ms for s in available.topics[0].starts] == [1000]
    assert expired.topics[0].starts == []
    assert expired.topics[0].text == '기존 논제'
    assert value == original
    assert MeetingDocument.model_validate_json(available.model_dump_json()) == available


def test_summary_contract_has_no_meeting_edit_fields():
    values = {'meeting_id': 'm', 'transcript_version': None, 'revision': 1,
              'metadata': {'title': '영상'}, 'summary': '영상 요약', 'topics': [], 'claims': []}
    assert VideoDocument(**values).document_kind == 'video_summary'
    with pytest.raises(ValidationError):
        VideoDocument(**values, decisions=[])


def test_persisted_legacy_result_read_does_not_need_transcript(context):  # noqa: F811
    _, repo = context
    meeting = repo.create_meeting(MeetingCreate(title='보존할 회의'))
    value = old_minutes(meeting['id'])
    with repo.write() as connection:
        connection.execute(text("INSERT INTO transcript_versions VALUES ('v',:meeting,NULL,'original','{}',0)"),
                           {'meeting': meeting['id']})
        connection.execute(text('''INSERT INTO minutes_revisions
            (id,meeting_id,transcript_version,parent_id,content_json,created_at)
            VALUES ('result',:meeting,'v',NULL,:content,1)'''),
            {'meeting': meeting['id'], 'content': json.dumps(value)})
        connection.execute(text("UPDATE meetings SET minutes_revision='result' WHERE id=:id"), {'id': meeting['id']})
        connection.execute(text("DELETE FROM transcript_versions WHERE id='v'"))
        assert not connection.execute(text('PRAGMA foreign_key_check')).all()
    result = read_minutes(repo, meeting['id'])
    assert result['content'] == value
    assert not result['source_available']
    assert result['document']['metadata']['title'] == '보존할 회의'
    assert result['document']['topics'][0]['starts'] == []
    assert repo.meeting(meeting['id'])['document_kind'] == 'meeting'
    assert repo.meeting(meeting['id'])['source_kind'] == 'file'


def test_populated_upgrade_preserves_result_parents_and_job_links(tmp_path):
    engine = make_engine(tmp_path / 'old.sqlite3')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/meeting_minutes/migrations'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0005')
    repo = Repository(engine)
    # Seed the historical schema directly; today's repository writes new columns.
    meeting = {'id': 'old-meeting'}
    job = {'id': 'old-job'}
    with repo.write() as connection:
        connection.execute(text('''INSERT INTO meetings(id,title,settings_json,created_at,updated_at)
            VALUES (:id,'과거 데이터',:settings,0,0)'''),
            {'id': meeting['id'], 'settings': MeetingCreate(title='과거 데이터').model_dump_json()})
        connection.execute(text('''INSERT INTO jobs(id,meeting_id,kind,state,stage,attempt_id,idempotency_key,
            request_hash,created_at,updated_at) VALUES ('old-job',:meeting,'summarize','COMPLETED','SAVE',
            'old-attempt','migration-key','request-hash',0,0)'''), {'meeting': meeting['id']})
        connection.execute(text("INSERT INTO transcript_versions VALUES ('v',:meeting,NULL,'original','{}',0)"),
                           {'meeting': meeting['id']})
        for version, parent in [('one', None), ('two', 'one')]:
            connection.execute(text('INSERT INTO minutes_revisions VALUES (:id,:meeting,\'v\',:parent,:content,0)'),
                {'id': version, 'meeting': meeting['id'], 'parent': parent, 'content': json.dumps(old_minutes(meeting['id']))})
        connection.execute(text("UPDATE jobs SET minutes_result_id='two' WHERE id=:id"), {'id': job['id']})
        before = connection.execute(text('SELECT * FROM minutes_revisions ORDER BY id')).all()
    migrate(engine)
    with engine.connect() as connection:
        grace = connection.execute(text('SELECT retention_grace_at FROM meetings WHERE id=:id'),
                                   {'id': meeting['id']}).scalar_one()
    migrate(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT * FROM minutes_revisions ORDER BY id')).all() == before
        assert connection.execute(text('SELECT minutes_result_id FROM jobs')).scalar_one() == 'two'
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0009'
        upgraded = connection.execute(text('SELECT * FROM meetings WHERE id=:id'),
                                      {'id': meeting['id']}).mappings().one()
        assert (upgraded['document_kind'], upgraded['source_kind']) == ('meeting', 'file')
        assert upgraded['retention_grace_at'] == grace and grace > 0
        policy = connection.execute(text('SELECT * FROM retention_policy')).mappings().one()
        assert (policy['enabled'], policy['media_days'], policy['transcript_days']) == (1, 7, 30)
        assert connection.execute(text('SELECT COUNT(*) FROM local_sessions')).scalar_one() == 0
        assert connection.execute(text('SELECT COUNT(*) FROM meeting_requests')).scalar_one() == 0
        assert not connection.execute(text('PRAGMA foreign_key_check')).all()
    engine.dispose()


def test_topic_returns_require_an_intervening_topic_and_preserve_sources():
    from meeting_minutes.documents import Topic, TopicStart, normalize_meeting_topics
    def point(n):
        return TopicStart(start_ms=n * 1000, end_ms=n * 1000 + 500, utterance_ids=[f'u{n}'])
    doc = MeetingDocument(meeting_id='m', transcript_version='v', revision=1,
        metadata={'title': '회의'}, summary='요약', decisions=[], action_items=[], open_questions=[], review_notes=[],
        topics=[Topic(id='a', title='', text='배포 일정 조정. 다음 문장', starts=[point(0), point(2), point(8)], source_segment_ids=['u0','u2','u8']),
                Topic(id='b', title='예산 검토', text='예산', starts=[point(4)]),
                Topic(id='c', title='인력 배치', text='인력', starts=[point(6)])])
    normalized = normalize_meeting_topics(doc)
    assert [s.start_ms for s in normalized.topics[0].starts] == [0, 8000]
    assert normalized.topics[0].starts[0].utterance_ids == ['u0', 'u2']
    assert normalized.topics[0].source_segment_ids == ['u0', 'u2', 'u8']
    assert normalized.topics[0].title == ''  # Never manufacture a title from body excerpts.
    assert len(doc.topics[0].starts) == 3 and not doc.topics[0].title
    assert normalize_meeting_topics(normalized) == normalized
    # Gaps or silence alone do not constitute a new discussion.
    doc.topics = doc.topics[:1]
    assert len(normalize_meeting_topics(doc).topics[0].starts) == 1


def test_generated_topic_rejects_blank_and_excessive_title():
    for title in ['   ', '가' * 61]:
        with pytest.raises(ValidationError):
            GeneratedTopic(id='t', title=title, text='내용', conclusion='', conclusion_kind='discussion',
                           ranges=[{'first':'u1','last':'u1'}])

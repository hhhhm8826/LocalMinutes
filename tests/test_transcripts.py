import json

import pytest
from sqlalchemy import text

from meeting_minutes.contracts import MeetingCreate, Segment
from meeting_minutes.repository import Conflict, Repository
from meeting_minutes.settings import Settings
from meeting_minutes.storage import make_engine, migrate
from meeting_minutes.transcripts import assign_speaker, attribute_segments, current_transcript, edit_transcript, merge_candidates


def test_word_assignment_splits_segment_and_preserves_unaligned_word():
    turns = [{'speaker_id': 'A', 'start_ms': 0, 'end_ms': 500}, {'speaker_id': 'B', 'start_ms': 500, 'end_ms': 1000}]
    aligned = [{'start': 0, 'end': 1, 'text': '안녕 hello unknown', 'words': [
        {'word': '안녕', 'start': 0, 'end': .4}, {'word': 'hello', 'start': .6, 'end': 1}, {'word': 'unknown'}]}]
    result = attribute_segments(aligned, turns, turns)
    assert all(Segment.model_validate(segment) for segment in result)
    assert [s['speaker_id'] for s in result] == ['A', 'B', None]
    assert result[-1]['text'] == 'unknown' and result[-1]['timing'] == 'segment'
    assert result[-1]['needs_review']
    assert assign_speaker(1100, 1200, turns, turns) == (None, True, False)


def test_overlap_and_short_acknowledgement_require_review():
    regular = [{'speaker_id': 'A', 'start_ms': 0, 'end_ms': 1000}, {'speaker_id': 'B', 'start_ms': 200, 'end_ms': 900}]
    assert assign_speaker(200, 600, regular[:1], regular) == ('A', True, True)
    assert assign_speaker(0, 100, regular[:1], regular) == ('A', True, False)


def test_embedding_candidates_need_multiple_clean_representatives():
    turns = [{'speaker_id': 'A', 'start_ms': 0, 'end_ms': 2000}, {'speaker_id': 'A', 'start_ms': 3000, 'end_ms': 5000},
             {'speaker_id': 'B', 'start_ms': 6000, 'end_ms': 8000}, {'speaker_id': 'B', 'start_ms': 9000, 'end_ms': 11000}]
    result = merge_candidates(['A', 'B'], turns, {'A': [1., 0.], 'B': [.9, .1]})
    assert len(result) == 1 and not result[0]['automatic'] and result[0]['similarity'] > .9
    assert merge_candidates(['A', 'B'], turns[:3], {'A': [1., 0.], 'B': [.9, .1]}) == []


def test_edit_merge_undo_preserves_original_and_optimistic_revision(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', config_dir=tmp_path / 'cfg', cache_dir=tmp_path / 'cache')
    settings.prepare()
    engine = make_engine(settings.database_path)
    migrate(engine)
    repo = Repository(engine)
    meeting = repo.create_meeting(MeetingCreate(title='편집'))
    original = {'speakers': {'A': {'name': '화자 1'}, 'B': {'name': '화자 2'}},
                'segments': [{'id': 's1', 'speaker_id': 'A', 'text': '안녕'}, {'id': 's2', 'speaker_id': 'B', 'text': 'hello'}],
                'merge_candidates': []}
    with repo.write() as connection:
        connection.execute(text("INSERT INTO transcript_versions VALUES ('v0',:meeting,NULL,'original',:content,0)"),
                           {'meeting': meeting['id'], 'content': json.dumps(original)})
        connection.execute(text("UPDATE meetings SET transcript_version='v0' WHERE id=:id"), {'id': meeting['id']})
    merged = edit_transcript(repo, meeting['id'], 1, {'type': 'merge', 'speaker_id': 'A', 'source_speaker_id': 'B'})
    assert all(segment['speaker_id'] == 'A' for segment in merged['content']['segments'])
    with pytest.raises(Conflict, match='REVISION_CONFLICT'):
        edit_transcript(repo, meeting['id'], 1, {'type': 'rename', 'speaker_id': 'A', 'name': '이름'})
    undone = edit_transcript(repo, meeting['id'], 2, {'type': 'undo'})
    assert undone['content']['segments'] == original['segments']
    assert undone['id'] != 'v0' and undone['kind'] == 'user'
    renamed = edit_transcript(repo, meeting['id'], 3, {'type': 'rename', 'speaker_id': 'A', 'name': '김 팀장'})
    assert renamed['content']['speakers']['A']['user_verified']
    assert current_transcript(repo, meeting['id'])['meeting_revision'] == 4
    with engine.connect() as connection:
        assert json.loads(connection.execute(text("SELECT content_json FROM transcript_versions WHERE id='v0'")).scalar_one()) == original
    engine.dispose()

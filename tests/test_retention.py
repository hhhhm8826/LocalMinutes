import json
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.documents import MeetingDocument
from meeting_minutes.library import export_minutes, media_info
from meeting_minutes.minutes_management import edit_minutes, queue_generation, read_minutes
from meeting_minutes.retention import RetentionUpdate, policy, reap_retention, update_policy
from meeting_minutes.repository import Conflict, Repository
from meeting_minutes.storage import make_engine, migrate
from meeting_minutes.deletion import meeting_file_lock
from test_minutes_management import complete, ready
from test_queue_media import context  # noqa: F401


def test_default_media7_transcript30_results_forever(context, monkeypatch, tmp_path):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'retention-minutes-fixture')
    original = complete(repo, queued, version)
    job = repo.jobs()[0]
    audio = settings.data_dir / 'artifacts' / 'cached.wav'
    audio.write_bytes(b'fixture')
    embedding = settings.data_dir / 'artifacts' / f'{job["id"]}-diarize.json'
    embedding.write_text('{"embedding":[1]}')
    orphan_audio = settings.data_dir / 'artifacts' / f'{job["id"]}-old.wav.part'
    orphan_audio.write_bytes(b'unregistered audio')
    scratch_root = tmp_path / 'scratch'
    scratch_root.mkdir()
    monkeypatch.setattr('meeting_minutes.temporary.ROOT', scratch_root)
    old_scratch = scratch_root / f'localminutes-codex-{job["id"]}-{"a" * 32}-old'
    old_scratch.mkdir()
    (old_scratch / 'prompt').write_text('old source')
    other_scratch = scratch_root / f'localminutes-codex-{"b" * 32}-{"a" * 32}-other'
    other_scratch.mkdir()
    with repo.write() as connection:
        value = original['content']
        value['topics'] = [{'id': 'topic', 'text': '점검 논의', 'source_segment_ids': ['s1']}]
        raw_original = json.dumps(value)
        connection.execute(text('UPDATE minutes_revisions SET content_json=:value WHERE id=:id'),
                           {'value': raw_original, 'id': original['id']})
        connection.execute(text('UPDATE meetings SET created_at=0,updated_at=9999999999 WHERE id=:id'), {'id': meeting['id']})
        for name, stage, path in [('a', 'EXTRACT', audio.name), ('b', 'DIARIZE', embedding.name)]:
            connection.execute(text('INSERT INTO stage_artifacts VALUES (:id,:job,:stage,:attempt,:input,:path,:sha,:now)'),
                {'id': name, 'job': job['id'], 'stage': stage, 'attempt': job['attempt_id'], 'input': 'fixture',
                 'path': path, 'sha': 'fixture', 'now': time.time()})
    assert policy(repo) == {'revision': 1, 'enabled': True, 'media_days': 7, 'transcript_days': 30}
    anchor = repo.meeting(meeting['id'])['input_received_at']
    reap_retention(repo, settings, anchor + 7 * 86400 - 1)
    assert audio.exists() and embedding.exists()
    reap_retention(repo, settings, anchor + 7 * 86400)
    assert not audio.exists() and not orphan_audio.exists() and embedding.exists()
    assert not list((settings.data_dir / 'media').iterdir())
    assert media_info(repo, settings, meeting['id'])['transcript_available']
    reap_retention(repo, settings, anchor + 30 * 86400 - 1)
    assert repo.meeting(meeting['id'])['transcript_version']
    reap_retention(repo, settings, anchor + 30 * 86400)
    assert not embedding.exists()
    assert not old_scratch.exists() and other_scratch.exists()
    assert repo.job(job['id'])['transcript_version'] is None
    result = read_minutes(repo, meeting['id'])
    assert result['document']['topics'][0]['starts'][0]['start_ms'] == 0
    assert not result['source_available']
    doc = MeetingDocument.model_validate(result['document'])
    doc.summary = '만료 후에도 편집'
    saved = edit_minutes(repo, meeting['id'], result['id'], result['meeting_revision'], doc)
    confirmed = edit_minutes(repo, meeting['id'], saved['id'], saved['meeting_revision'], confirm=True)
    assert '만료 후에도 편집' in export_minutes(repo, meeting['id'])
    reap_retention(repo, settings, anchor + 1000 * 86400)
    assert read_minutes(repo, meeting['id'])['id'] == confirmed['id']
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT content_json FROM minutes_revisions WHERE id=:id'), {'id': original['id']}).scalar_one() == raw_original
        assert connection.execute(text('SELECT count(*) FROM transcript_versions')).scalar_one() == 0
        assert not connection.execute(text('PRAGMA foreign_key_check')).all()
    with repo.write() as connection:
        connection.execute(text("UPDATE jobs SET state='FAILED' WHERE id=:id"), {'id': job['id']})
    with pytest.raises(Conflict, match='SOURCE_EXPIRED'):
        repo.retry(job['id'])


def test_retention_skips_pending_jobs_and_policy_conflicts(context):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with repo.write() as connection:
        connection.execute(text("UPDATE jobs SET state='QUEUED' WHERE meeting_id=:id"), {'id': meeting['id']})
    update_policy(repo, RetentionUpdate(expected_revision=1, enabled=True, media_days=1, transcript_days=1))
    with pytest.raises(Conflict):
        update_policy(repo, RetentionUpdate(expected_revision=1, enabled=False))
    reap_retention(repo, settings, meeting['input_received_at'] + 86401)
    assert list((settings.data_dir / 'media').iterdir())
    assert repo.meeting(meeting['id'])['transcript_version']


@pytest.mark.parametrize('old,expected', [
    ((1, 0, None, None, None), (True, 7, 30)),
    ((2, 0, None, None, None), (False, None, None)),
    ((2, 1, 3, 14, 90), (True, 14, 90)),
    ((2, 1, None, 14, 60), (True, None, 60)),
])
def test_policy_migration_grace_and_idempotence(tmp_path, old, expected):
    engine = make_engine(tmp_path / 'old.sqlite3')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/meeting_minutes/migrations'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0007')
        connection.exec_driver_sql('UPDATE retention_policy SET revision=?,enabled=?,original_days=?,audio_days=?,text_days=?', old)
        connection.exec_driver_sql("INSERT INTO meetings(id,title,settings_json,created_at,updated_at) VALUES ('m','old','{}',1,1)")
        connection.exec_driver_sql("INSERT INTO media_assets(id,meeting_id,original_name,stored_name,size_bytes,sha256,created_at) VALUES ('a','m','a','a',1,'hash',2)")
    before = time.time()
    migrate(engine)
    repo = Repository(engine)
    current = policy(repo)
    assert (current['enabled'], current['media_days'], current['transcript_days']) == expected
    snapshot = repo.meeting('m')
    assert snapshot['input_received_at'] == 2
    assert snapshot['retention_grace_at'] >= before
    migrate(engine)
    assert repo.meeting('m') == snapshot
    assert policy(repo) == current
    engine.dispose()


@pytest.mark.parametrize('state', ['BLOCKED', 'INTERRUPTED'])
def test_terminal_wait_states_do_not_suspend_retention(context, state):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    job = repo.jobs()[0]
    with repo.write() as connection:
        connection.execute(text("UPDATE jobs SET state='RUNNING',finished_at=NULL WHERE id=:id"), {'id': job['id']})
    repo.finish(job['id'], job['attempt_id'], state, 'TEST_WAIT')
    assert repo.job(job['id'])['finished_at'] is not None
    assert repo.job(job['id'])['pid'] is None
    anchor = meeting['input_received_at']
    reap_retention(repo, settings, anchor + 7 * 86400)
    assert not list((settings.data_dir / 'media').iterdir())
    assert repo.meeting(meeting['id'])['media_expired_at'] is not None
    reap_retention(repo, settings, anchor + 30 * 86400)
    assert repo.meeting(meeting['id'])['transcript_expired_at'] is not None
    assert repo.meeting(meeting['id'])['transcript_version'] is None
    with pytest.raises(Conflict, match='SOURCE_EXPIRED'):
        repo.retry(job['id'])


@pytest.mark.parametrize('state', ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
def test_retention_keeps_actual_active_jobs(context, state):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with repo.write() as connection:
        connection.execute(text('UPDATE jobs SET state=:state WHERE meeting_id=:id'), {'state': state, 'id': meeting['id']})
    reap_retention(repo, settings, meeting['input_received_at'] + 31 * 86400)
    assert list((settings.data_dir / 'media').iterdir())
    assert repo.meeting(meeting['id'])['transcript_version'] is not None


def test_retention_respects_open_file_lock(context):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with meeting_file_lock(settings, meeting['id']):
        reap_retention(repo, settings, meeting['input_received_at'] + 31 * 86400)
        assert repo.meeting(meeting['id'])['transcript_version'] is not None
    reap_retention(repo, settings, meeting['input_received_at'] + 31 * 86400)
    assert repo.meeting(meeting['id'])['transcript_version'] is None

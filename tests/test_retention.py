import time

from sqlalchemy import text

from meeting_minutes.retention import RetentionUpdate, policy, reap_retention, update_policy
from meeting_minutes.repository import Conflict
import pytest
from test_minutes_management import complete, ready
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.minutes_management import queue_generation
from test_queue_media import context  # noqa: F401


def test_retention_default_and_separate_audio_text_lifetimes(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    queued = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'retention-minutes-fixture')
    complete(repo, queued, version)
    job = repo.jobs()[0]
    audio = settings.data_dir / 'artifacts' / 'cached.wav'
    audio.write_bytes(b'fixture')
    embedding = settings.data_dir / 'artifacts' / f'{job["id"]}-diarize.json'
    embedding.write_text('{"embedding":[1]}')
    with repo.write() as connection:
        for name, stage, path in [('a', 'EXTRACT', audio.name), ('b', 'DIARIZE', embedding.name)]:
            connection.execute(text('INSERT INTO stage_artifacts VALUES (:id,:job,:stage,:attempt,:input,:path,:sha,:now)'),
                {'id': name, 'job': job['id'], 'stage': stage, 'attempt': job['attempt_id'], 'input': 'fixture',
                 'path': path, 'sha': 'fixture', 'now': time.time()})
    future = meeting['created_at'] + 10 * 86400
    reap_retention(repo, settings, future)
    assert audio.exists() and embedding.exists() and repo.meeting(meeting['id'])['transcript_version']
    assert policy(repo)['enabled'] is False
    update_policy(repo, RetentionUpdate(expected_revision=1, enabled=True, audio_days=1))
    reap_retention(repo, settings, future)
    assert not audio.exists() and embedding.exists() and repo.meeting(meeting['id'])['transcript_version']
    assert list((settings.data_dir / 'media').iterdir())
    with pytest.raises(Conflict):
        update_policy(repo, RetentionUpdate(expected_revision=1, enabled=False))
    update_policy(repo, RetentionUpdate(expected_revision=2, enabled=True, text_days=2))
    reap_retention(repo, settings, future)
    assert not embedding.exists() and not repo.meeting(meeting['id'])['transcript_version']
    assert list((settings.data_dir / 'media').iterdir())
    assert repo.job(job['id'])['transcript_version'] is None
    update_policy(repo, RetentionUpdate(expected_revision=3, enabled=True, original_days=3))
    reap_retention(repo, settings, future)
    assert not list((settings.data_dir / 'media').iterdir())


def test_retention_skips_pending_jobs(context):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    with repo.write() as connection:
        connection.execute(text("UPDATE jobs SET state='QUEUED' WHERE meeting_id=:id"), {'id': meeting['id']})
    update_policy(repo, RetentionUpdate(expected_revision=1, enabled=True, original_days=1, audio_days=1, text_days=1))
    reap_retention(repo, settings, meeting['created_at'] + 86401)
    assert list((settings.data_dir / 'media').iterdir())
    assert repo.meeting(meeting['id'])['transcript_version']

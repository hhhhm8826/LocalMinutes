"""시도별 격리 프로세스. 부모가 식별 정보를 저장한 뒤에만 실행한다."""
import argparse
import json
import os
import sys
import time

from sqlalchemy import text

from .files import file_hash, safe_file, sync_directory
from .media import extract, probe
from .processes import ProcessFailure
from .repository import Conflict, Repository, identifier
from .settings import Settings
from .speech_pipeline import run_speech
from .storage import make_engine


def persist_artifact(repository, settings, job, partial, stage, fingerprint):
    with partial.open('rb') as stream:
        os.fsync(stream.fileno())
    sha = file_hash(partial)
    name = f'{job["id"]}-{job["attempt_id"]}-{stage.lower()}.wav'
    target = safe_file(settings.data_dir / 'artifacts', name)
    with repository.write() as connection:
        repository.assert_current(connection, job)
        partial.replace(target)
        sync_directory(target.parent)
        connection.execute(text("""INSERT INTO stage_artifacts
            (id,job_id,stage,attempt_id,input_fingerprint,path,sha256,created_at)
            VALUES (:id,:job,:stage,:attempt,:fingerprint,:path,:sha,:now)"""),
            {'id': identifier(), 'job': job['id'], 'stage': stage, 'attempt': job['attempt_id'],
             'fingerprint': fingerprint, 'path': name, 'sha': sha, 'now': time.time()})
    return target


def run(repository, settings, job):
    if not job['media_id'] and not job['transcript_version']:
        meeting = repository.meeting(job['meeting_id'])
        if meeting['source_kind'] == 'youtube':
            from .youtube_runner import acquire_job
            job = acquire_job(repository, settings, job)
    if job['transcript_version']:
        meeting = json.loads(repository.meeting(job['meeting_id'])['settings_json'])
        if meeting['allow_external_text']:
            from .minutes_pipeline import run_minutes
            return run_minutes(repository, settings, job)
        return 'COMPLETED_TRANSCRIPT_ONLY', None
    media = repository.media(job['media_id'])
    source = safe_file(settings.data_dir / 'media', media['stored_name'])
    if file_hash(source) != media['sha256']:
        raise ProcessFailure('MEDIA_CHECKSUM_MISMATCH')
    repository.stage(job, 'VALIDATE')
    tracks, duration = probe(source, settings.max_duration_seconds)
    repository.set_media_metadata(job, duration, tracks)
    selected = media['selected_track']
    defaults = [track for track in tracks if track['default']]
    if selected is None:
        if len(tracks) == 1:
            selected = tracks[0]['index']
        elif len(defaults) == 1:
            selected = defaults[0]['index']
        else:
            return 'BLOCKED', 'AUDIO_TRACK_REQUIRED'
    if selected not in [track['index'] for track in tracks]:
        raise ProcessFailure('INVALID_AUDIO_TRACK')
    fingerprint = json.dumps({'sha256': media['sha256'], 'track': selected,
                              'format': 'pcm_s16le/16000/mono', 'adapter': 1}, sort_keys=True)
    repository.stage(job, 'EXTRACT')
    with repository.engine.connect() as connection:
        previous = connection.execute(text("""SELECT * FROM stage_artifacts
            WHERE job_id=:job AND stage='EXTRACT' AND input_fingerprint=:fingerprint
            ORDER BY created_at DESC LIMIT 1"""), {'job': job['id'], 'fingerprint': fingerprint}).mappings().first()
    if previous:
        path = safe_file(settings.data_dir / 'artifacts', previous['path'])
        if path.is_file() and file_hash(path) == previous['sha256']:
            repository.stage(job, 'TRANSCRIBE', {'reused': 'EXTRACT'})
            return run_speech(repository, settings, job, path)
    partial = safe_file(settings.data_dir / 'artifacts', f'{job["id"]}-{job["attempt_id"]}.wav.part')
    try:
        duration = extract(source, partial, selected, settings.max_duration_seconds)
        repository.set_media_metadata(job, duration, tracks)
        path = persist_artifact(repository, settings, job, partial, 'EXTRACT', fingerprint)
    finally:
        partial.unlink(missing_ok=True)
    repository.stage(job, 'TRANSCRIBE')
    return run_speech(repository, settings, job, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    if sys.stdin.readline().strip() != 'START':
        return
    os.umask(0o077)
    settings = Settings()
    engine = make_engine(settings.database_path)
    repository = Repository(engine)
    job = repository.job(args.job)
    if job['attempt_id'] != args.attempt:
        return
    try:
        state, reason = run(repository, settings, job)
        # 완료는 부모가 그룹 내 자식의 종료까지 확인한 후 확정한다.
        print(json.dumps({'state': state, 'reason': reason}), flush=True)
    except Conflict:
        print(json.dumps({'state': 'CANCELLED', 'reason': None}), flush=True)
    except ProcessFailure as exc:
        print(json.dumps({'state': 'FAILED', 'reason': exc.code}), flush=True)
    except Exception:
        print(json.dumps({'state': 'FAILED', 'reason': 'STAGE_FAILED'}), flush=True)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()

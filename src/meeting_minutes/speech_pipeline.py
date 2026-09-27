"""Fenced, checksummed speech checkpoints and immutable original transcript."""
from contextlib import redirect_stdout
import fcntl
import hashlib
import json
import os
import resource
import sys
import time

import psutil
from sqlalchemy import text

from .files import file_hash, safe_file, sync_directory
from .repository import identifier
from .speech import MODEL_REVISIONS, ModelUnavailable, SpeechModels
from .transcripts import attribute_segments, merge_candidates


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cached_checkpoint(repository, settings, job, stage, inputs):
    digest = fingerprint(inputs)
    with repository.engine.connect() as connection:
        rows = connection.execute(text('''SELECT path,sha256 FROM stage_artifacts
            WHERE job_id=:job AND stage=:stage AND input_fingerprint=:digest ORDER BY created_at DESC'''),
            {'job': job['id'], 'stage': stage, 'digest': digest}).mappings().all()
    for row in rows:
        path = safe_file(settings.data_dir / 'artifacts', row['path'])
        if path.is_file() and file_hash(path) == row['sha256']:
            return True, json.loads(path.read_text())
    return False, None


def checkpoint(repository, settings, job, stage, inputs, calculate):
    digest = fingerprint(inputs)
    repository.stage(job, stage)
    found, value = cached_checkpoint(repository, settings, job, stage, inputs)
    if found:
        repository.stage(job, stage, {'reused': stage})
        return value
    started = time.monotonic()
    value = calculate()
    # Serialize before publishing; non-finite embeddings must never enter storage.
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
    name = f'{job["id"]}-{job["attempt_id"]}-{stage.lower()}-{digest[:24]}.json'
    target = safe_file(settings.data_dir / 'artifacts', name)
    partial = safe_file(target.parent, name + '.part')
    try:
        with partial.open('x', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        sha = file_hash(partial)
        with repository.write() as connection:
            repository.assert_current(connection, job)
            partial.replace(target)
            sync_directory(target.parent)
            connection.execute(text('''INSERT INTO stage_artifacts
                (id,job_id,stage,attempt_id,input_fingerprint,path,sha256,created_at)
                VALUES (:id,:job,:stage,:attempt,:digest,:path,:sha,:now)'''),
                {'id': identifier(), 'job': job['id'], 'stage': stage, 'attempt': job['attempt_id'],
                 'digest': digest, 'path': name, 'sha': sha, 'now': time.time()})
            connection.execute(text('''INSERT INTO usage_records(id,job_id,attempt_id,stage,metrics_json,created_at)
                VALUES (:id,:job,:attempt,:stage,:metrics,:now)'''),
                {'id': identifier(), 'job': job['id'], 'attempt': job['attempt_id'], 'stage': stage,
                 'metrics': json.dumps({'wall_seconds': time.monotonic() - started, 'cache_hit': False,
                                        'rss_bytes': psutil.Process().memory_info().rss,
                                        'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}),
                 'now': time.time()})
    finally:
        partial.unlink(missing_ok=True)
    return value


def save_original(repository, job, content):
    with repository.write() as connection:
        repository.assert_current(connection, job)
        existing = connection.execute(text('SELECT transcript_version FROM jobs WHERE id=:id'),
                                      {'id': job['id']}).scalar_one()
        if existing:
            return existing
        version, now = identifier(), time.time()
        connection.execute(text('''INSERT INTO transcript_versions(id,meeting_id,parent_id,kind,content_json,created_at)
            VALUES (:id,:meeting,NULL,'original',:content,:now)'''),
            {'id': version, 'meeting': job['meeting_id'], 'content': json.dumps(content, ensure_ascii=False, allow_nan=False), 'now': now})
        # A retry must never replace an owner's edited version.
        connection.execute(text('''UPDATE meetings SET transcript_version=:version,revision=revision+1,updated_at=:now
            WHERE id=:meeting AND transcript_version IS NULL'''),
            {'version': version, 'meeting': job['meeting_id'], 'now': now})
        connection.execute(text('UPDATE jobs SET transcript_version=:version WHERE id=:id'),
                           {'version': version, 'id': job['id']})
    return version


def run_speech(repository, settings, job, audio_path):
    meeting = json.loads(repository.meeting(job['meeting_id'])['settings_json'])
    with (settings.cache_dir / 'heavy.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'BLOCKED', 'HEAVY_RESOURCE_BUSY'
        try:
            # Library progress output must not corrupt the worker's single JSON result.
            with redirect_stdout(sys.stderr):
                models = SpeechModels(settings)
                audio = None
                def load_audio():
                    nonlocal audio
                    if audio is None:
                        audio = models.audio(audio_path)
                    return audio
                inputs = {'audio_sha256': file_hash(audio_path), 'revisions': MODEL_REVISIONS,
                          'language': meeting['language'], 'speakers': meeting['speakers'],
                          'threads': settings.threads, 'adapter': 1}
                asr_inputs = {key: inputs[key] for key in ('audio_sha256', 'language', 'threads', 'adapter')}
                asr_inputs['revision'] = MODEL_REVISIONS['asr']
                transcript = checkpoint(repository, settings, job, 'TRANSCRIBE', asr_inputs,
                                        lambda: models.transcribe(load_audio(), meeting['language']))
                align_inputs = {'source': fingerprint(transcript), 'audio_sha256': inputs['audio_sha256'],
                                'ko_revision': MODEL_REVISIONS['ko_align'],
                                'en_model': 'torchaudio-WAV2VEC2_ASR_BASE_960H', 'adapter': 1}
                aligned = checkpoint(repository, settings, job, 'ALIGN', align_inputs,
                                     lambda: models.align(load_audio(), transcript))
                diar_inputs = {'audio_sha256': inputs['audio_sha256'], 'aligned': fingerprint(aligned),
                               'speakers': meeting['speakers'], 'revision': MODEL_REVISIONS['diarization'], 'adapter': 1}
                diarized = checkpoint(repository, settings, job, 'DIARIZE', diar_inputs,
                                      lambda: models.diarize(load_audio(), meeting['speakers']))
                repository.stage(job, 'ATTRIBUTE')
                labels = sorted(diarized['embeddings'])
                content = {'schema_version': 1, 'language': transcript['language'],
                           'speakers': {label: {'name': f'화자 {i+1}', 'user_verified': False} for i, label in enumerate(labels)},
                           'segments': attribute_segments(aligned['segments'], diarized['exclusive'], diarized['regular']),
                           'merge_candidates': merge_candidates(labels, diarized['regular'], diarized['embeddings']),
                           'original_asr': transcript, 'alignment': aligned, 'diarization': diarized,
                           'model_metadata': inputs, 'warnings': transcript.get('warnings', [])}
                repository.stage(job, 'SAVE')
                save_original(repository, job, content)
        except ModelUnavailable as exc:
            return 'BLOCKED', str(exc)
    if meeting['allow_external_text']:
        from .minutes_pipeline import run_minutes
        return run_minutes(repository, settings, job)
    return 'COMPLETED_TRANSCRIPT_ONLY', None

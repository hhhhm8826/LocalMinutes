"""Bounded child supervision and publish only after complete local validation."""
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time

from sqlalchemy import text

from .files import file_hash, safe_file, sync_directory
from .media import probe
from .processes import ProcessFailure
from .repository import identifier
from .temporary import attempt_prefix
from .youtube_sandbox import sandbox_command


def supervise(command, scratch, stage, *, seconds=960, max_bytes=2_032_000_000):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/bin:/bin'})
    selector = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        selector.register(stream, selectors.EVENT_READ)
    deadline, size, buffer, result, error = time.monotonic() + seconds, 0, b'', None, None
    child_started = False
    try:
        while selector.get_map():
            if time.monotonic() > deadline:
                raise ProcessFailure('YOUTUBE_TRANSFER_TIMEOUT')
            disk_bytes = 0
            for path in scratch.rglob('*'):
                if path.is_symlink():
                    raise ProcessFailure('YOUTUBE_UNSAFE_OUTPUT')
                if path.is_file():
                    disk_bytes += path.stat().st_size
            if disk_bytes > max_bytes:
                raise ProcessFailure('FILE_SIZE_LIMIT')
            for key, _ in selector.select(.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                size += len(chunk)
                if size > 1_000_000:
                    raise ProcessFailure('YOUTUBE_OUTPUT_LIMIT')
                if key.fileobj is process.stderr:
                    continue
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    record = json.loads(line)
                    if record.get('stage') in {'SOURCE_CHECK', 'DOWNLOAD'}:
                        child_started = True
                        stage(record['stage'])
                    if 'result' in record:
                        if result is not None:
                            raise ProcessFailure('YOUTUBE_INVALID_RESULT')
                        result = record['result']
                    error = record.get('error', error)
        process.wait(timeout=max(.1, deadline - time.monotonic()))
        if error or process.returncode or result is None:
            raise ProcessFailure(error or ('YOUTUBE_ACCESS_FAILED' if child_started else 'YOUTUBE_PROCESS_START_FAILED'))
        return result
    finally:
        # Namespace PID 1 dying tears down all descendants, including Deno.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()


def acquire_job(repository, settings, job):
    meeting = repository.meeting(job['meeting_id'])
    source = json.loads(meeting['source_metadata_json'])['source_url']
    with tempfile.TemporaryDirectory(prefix=attempt_prefix(job['id'], job['attempt_id'])) as temporary:
        scratch = Path(temporary)
        output = scratch / 'download'
        output.mkdir()
        command = sandbox_command(settings, scratch, [sys.executable, '-m', 'meeting_minutes.youtube_child',
                                                      source, output, settings.youtube_deno])
        result = supervise(command, scratch, lambda stage: repository.stage(job, stage))
        path = output / 'audio.source'
        if path.is_symlink() or result['path'] != str(path) or not path.is_file():
            raise ProcessFailure('YOUTUBE_UNSAFE_OUTPUT')
        size = path.stat().st_size
        sha = file_hash(path)
        if not 0 < size <= settings.max_upload_bytes or size != result['size_bytes'] or sha != result['sha256']:
            raise ProcessFailure('YOUTUBE_CHECKSUM_MISMATCH')
        tracks, duration = probe(path, settings.max_duration_seconds)
        if not duration:
            raise ProcessFailure('YOUTUBE_DURATION_UNKNOWN')
        expected = result['metadata'].get('duration_seconds')
        if (isinstance(expected, bool) or not isinstance(expected, (int, float))
                or not math.isfinite(expected) or expected <= 0):
            raise ProcessFailure('YOUTUBE_DURATION_UNKNOWN')
        # Metadata may round seconds and audio codecs add small padding. A valid
        # container/hash alone must not let a truncated download become complete.
        tolerance = min(2000, max(250, expected * 10))
        if abs(duration - expected * 1000) > tolerance:
            raise ProcessFailure('YOUTUBE_DOWNLOAD_INCOMPLETE')
        stored = f'{job["meeting_id"]}-{job["attempt_id"]}.source'
        target = safe_file(settings.data_dir / 'media', stored)
        partial = safe_file(settings.data_dir / 'media', stored + '.part')
        published = False
        try:
            with path.open('rb') as incoming, partial.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            with repository.write() as connection:
                repository.assert_current(connection, job)
                partial.replace(target)
                sync_directory(target.parent)
                media_id, now = identifier(), time.time()
                connection.execute(text("""INSERT INTO media_assets(id,meeting_id,original_name,stored_name,size_bytes,sha256,
                    duration_ms,tracks_json,created_at) VALUES (:id,:meeting,'youtube-audio',:stored,:size,:sha,:duration,:tracks,:now)"""),
                    {'id': media_id, 'meeting': job['meeting_id'], 'stored': stored, 'size': size, 'sha': sha,
                     'duration': duration, 'tracks': json.dumps(tracks), 'now': now})
                connection.execute(text('UPDATE jobs SET media_id=:media WHERE id=:id'), {'media': media_id, 'id': job['id']})
                connection.execute(text('UPDATE meetings SET source_metadata_json=:source,input_received_at=COALESCE(input_received_at,:now) WHERE id=:id'),
                    {'source': json.dumps(result['metadata']), 'now': now, 'id': job['meeting_id']})
                options = json.loads(meeting['settings_json'])
                if not options.get('title') or options.get('title') == 'YouTube 영상':
                    options['title'] = result['metadata']['title']
                    connection.execute(text('UPDATE meetings SET title=:title,settings_json=:options WHERE id=:id'),
                        {'id': job['meeting_id'], 'title': options['title'], 'options': json.dumps(options, ensure_ascii=False)})
            published = True
        finally:
            partial.unlink(missing_ok=True)
            if not published:
                target.unlink(missing_ok=True)
    return repository.job(job['id'])

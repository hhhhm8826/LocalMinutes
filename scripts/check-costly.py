"""Opt-in real executions with durable, shared budgets and private output."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reserve(path, kind, fixture_hash):
    # This budget is supplied explicitly by the operator; never create/reset it.
    with path.open('r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        budget = json.load(stream)
        maximum = budget.get('max_' + kind)
        if (type(maximum) is not int or maximum <= 0
                or fixture_hash not in budget.get('allowed_fixture_sha256', [])):
            raise ValueError('No positive budget for this exact fixture')
        used = budget.get('reserved_' + kind, 0)
        if type(used) is not int or used < 0 or used >= maximum:
            raise ValueError('Execution budget exhausted')
        wall = budget.get('max_wall_seconds')
        if type(wall) is not int or not 10 <= wall <= 14400:
            raise ValueError('max_wall_seconds must be 10..14400')
        budget['reserved_' + kind] = used + 1
        stream.seek(0)
        json.dump(budget, stream, indent=2)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        return wall


def model(args, fixture, output, report):
    import psutil
    from sqlalchemy import text
    from meeting_minutes.contracts import MeetingCreate
    from meeting_minutes.media import probe
    from meeting_minutes.repository import Repository, identifier
    from meeting_minutes.settings import Settings
    from meeting_minutes.storage import make_engine, migrate
    from meeting_minutes.worker import Worker

    source = Path(fixture['path'])
    if not source.is_absolute() or not fixture.get('license') or sha(source) != fixture['sha256']:
        raise ValueError('A licensed absolute fixture path and matching SHA256 are required')
    tracks, duration = probe(source)
    if len(tracks) != 1 or duration is None or (args.mode == 'soak' and duration != 7200000):
        raise ValueError('Use one audio track; soak requires exactly 7200 seconds')
    settings = Settings(data_dir=output / 'data', config_dir=output / 'config', worker_enabled=False)
    report['configuration'] = {'threads': settings.threads, 'device': 'cpu',
                               'language': fixture['language'], 'allow_external_text': False}
    settings.prepare()
    target = settings.data_dir / 'media' / ('fixture' + source.suffix)
    shutil.copyfile(source, target)
    if sha(target) != fixture['sha256']:
        raise ValueError('Fixture changed during copy')
    engine = make_engine(settings.database_path)
    migrate(engine)
    repo = Repository(engine)
    worker = Worker(settings, engine)
    if not worker.acquire():
        raise RuntimeError('Diagnostic worker already running')
    done = threading.Event()
    observer = None
    try:
        meeting = repo.create_meeting(MeetingCreate(title='Explicit local diagnostic',
                                      language=fixture['language'], allow_external_text=False))
        job, _ = repo.register_media(meeting['id'], target.name, target.name, target.stat().st_size,
                                    fixture['sha256'], identifier(), fixture['sha256'])
        wall = reserve(args.budget, 'model_runs', report['fixture_manifest_sha256'])
        report.update(job_id=job['id'], duration_ms=duration, max_tree_rss_bytes=0,
                      max_swap_bytes=psutil.swap_memory().used, generation_calls=0)
        start = time.monotonic()
        def observe():
            while not done.wait(.5):
                if time.monotonic() - start > wall:
                    worker.stopping = True
                current = repo.job(job['id'])
                report['max_swap_bytes'] = max(report['max_swap_bytes'], psutil.swap_memory().used)
                if current['pid']:
                    try:
                        process = psutil.Process(current['pid'])
                        report['max_tree_rss_bytes'] = max(report['max_tree_rss_bytes'],
                            sum(p.memory_info().rss for p in [process, *process.children(recursive=True)]))
                    except psutil.Error:
                        pass
        observer = threading.Thread(target=observe)
        observer.start()
        worker.execute(repo.claim())
        report['wall_seconds'] = time.monotonic() - start
        final = repo.job(job['id'])
        if final['state'] != 'COMPLETED_TRANSCRIPT_ONLY':
            raise RuntimeError('Pipeline did not complete: ' + final['state'])
        with engine.connect() as connection:
            content = json.loads(connection.execute(text('SELECT content_json FROM transcript_versions WHERE id=:id'),
                                 {'id': final['transcript_version']}).scalar_one())
            artifacts = [dict(row) for row in connection.execute(
                text('SELECT stage,path,sha256 FROM stage_artifacts')).mappings()]
            report['stage_metrics'] = [dict(stage=row[0], metrics=json.loads(row[1]))
                for row in connection.execute(text('SELECT stage,metrics_json FROM usage_records ORDER BY created_at'))]
        segments = content['segments']
        if not segments or not all(0 <= s['start_ms'] <= s['end_ms'] <= duration + 100 for s in segments):
            raise ValueError('Invalid output timeline')
        if not all(a['start_ms'] <= b['start_ms'] for a, b in zip(segments, segments[1:])):
            raise ValueError('Unordered output timeline')
        if not all(sha(settings.data_dir / 'artifacts' / a['path']) == a['sha256'] for a in artifacts):
            raise ValueError('Invalid artifact checksum')
        report.update(segments=len(segments), artifacts=artifacts, timeline_valid=True,
                      real_time_factor=report['wall_seconds'] / (duration / 1000),
                      quality_ground_truth=None, scope='Pipeline execution and stability, not accuracy scores')
    finally:
        done.set()
        if observer:
            observer.join()
        worker.close()
        engine.dispose()


def live(args, fixture, output, report):
    from meeting_minutes.codex_provider import CodexCliProvider
    from meeting_minutes.contracts import Minutes
    from meeting_minutes.minutes_validation import text_payload, validate_minutes
    from meeting_minutes.settings import Settings

    if fixture.get('classification') not in {'public', 'fictional'}:
        raise ValueError('Live diagnostics accept public or fictional text only')
    meeting = fixture['meeting']
    if meeting.get('allow_external_text') is not True:
        raise ValueError('Explicit external text consent is required')
    payload = text_payload('diagnostic', 'fixture-v1', 1, meeting, fixture['transcript'])
    # Read limits before provider launch, reserve durably at the generation boundary.
    budget = json.loads(args.budget.read_text())
    wall = budget.get('max_wall_seconds')
    if type(wall) is not int or not 10 <= wall <= 1800:
        raise ValueError('Live max_wall_seconds must be 10..1800')
    settings = Settings(codex_timeout_seconds=wall)
    report['configuration'] = {'model': settings.codex_model, 'timeout_seconds': wall,
                               'input_bytes': settings.codex_input_bytes}
    value, metrics = CodexCliProvider(settings).generate(payload, Minutes.model_json_schema(),
        lambda: reserve(args.budget, 'codex_calls', report['fixture_manifest_sha256']))
    minutes = validate_minutes(value, payload, meeting)
    (output / 'minutes.json').write_text(minutes.model_dump_json(indent=2))
    report.update(metrics=metrics, scope='Live CLI structured output and local evidence validation')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['model', 'soak', 'live-codex'])
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--budget', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--user-requested-soak', action='store_true',
                        help='Confirm the user explicitly requested this 120-minute execution')
    args = parser.parse_args()
    if args.mode == 'soak' and not args.user_requested_soak:
        parser.error('soak requires a current explicit user request and --user-requested-soak')
    os.umask(0o077)
    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    report = {'status': 'RUNNING', 'mode': args.mode,
              'fixture_manifest_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
              'inputs': {str(path.relative_to(root)): sha(path) for path in
                         [root / 'uv.lock', *sorted((root / 'src').rglob('*.py')),
                          root / 'src/meeting_minutes/model_manifest.json']}}
    try:
        (live if args.mode == 'live-codex' else model)(args, fixture, output, report)
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error_type=type(error).__name__)
    finally:
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'status': report['status'], 'result': str(output / 'result.json')}))
    raise SystemExit(0 if report['status'] == 'PASS' else 2)


if __name__ == '__main__':
    main()

"""브라우저 시험 전용 서버. 임시 데이터와 가상 전사만 사용하며 워커를 시작하지 않는다."""
import hashlib
from pathlib import Path
import tempfile
import time

from sqlalchemy import text
import uvicorn

from meeting_minutes.api import create_app
from meeting_minutes.contracts import MeetingCreate, Minutes
from meeting_minutes.minutes_storage import save_minutes
from meeting_minutes.repository import Repository
from meeting_minutes.security import prepare_owner_key
from meeting_minutes.settings import Settings
from meeting_minutes.speech_pipeline import save_original
from meeting_minutes.storage import make_engine, migrate
from test_queue_media import wav_bytes


with tempfile.TemporaryDirectory(prefix='minutes-browser-') as directory:
    root = Path(directory)
    settings = Settings(data_dir=root / 'data', config_dir=root / 'config', cache_dir=root / 'cache',
                        web_dir=Path(__file__).resolve().parents[1] / 'apps/web/dist',
                        origin='http://127.0.0.1:8877', worker_enabled=False)
    settings.prepare()
    prepare_owner_key(settings.owner_key_path)
    settings.owner_key_path.write_text('browser-test-owner-key')
    engine = make_engine(settings.database_path)
    migrate(engine)
    repo = Repository(engine)
    meeting = repo.create_meeting(MeetingCreate(title='브라우저 검증 회의'))
    audio = wav_bytes(5)
    (settings.data_dir / 'media' / 'fixture.wav').write_bytes(audio)
    repo.register_media(meeting['id'], 'fixture.wav', 'fixture.wav', len(audio), hashlib.sha256(audio).hexdigest(),
                        'browser-fixture', 'browser-fixture')
    job = repo.claim()
    transcript = save_original(repo, job, {'speakers': {'A': {'name': '화자 1'}, 'B': {'name': '화자 2'}},
        'merge_candidates': [{'left': 'A', 'right': 'B', 'similarity': .9}],
        'segments': [{'id': 's1', 'text': '금요일 배포를 제안합니다.', 'start_ms': 0, 'end_ms': 1000, 'speaker_id': 'A'},
                     {'id': 's2', 'text': '배포를 취소하고 내일 검토합시다. 담당자는 미정입니다.', 'start_ms': 2000, 'end_ms': 4000, 'speaker_id': 'B'}]})
    content = Minutes(meeting_id=meeting['id'], transcript_version=transcript, revision=1, summary='배포를 취소하고 검토하기로 했다.',
        topics=[], decisions=[{'id': 'd1', 'text': '배포 취소', 'source_segment_ids': ['s2']}],
        action_items=[{'id': 'a1', 'task': '검토', 'owner_speaker_id': None, 'due_date': None,
                       'due_date_original_expression': '내일', 'source_segment_ids': ['s2']}], open_questions=[], review_notes=[])
    save_minutes(repo, job, content, None)
    (settings.data_dir / 'artifacts' / 'fixture-audio.wav').write_bytes(audio)
    with repo.write() as connection:
        connection.execute(text('INSERT INTO stage_artifacts VALUES (:id,:job,:stage,:attempt,:input,:path,:sha,:now)'),
            {'id': 'fixture-audio', 'job': job['id'], 'stage': 'EXTRACT', 'attempt': job['attempt_id'],
             'input': 'fixture', 'path': 'fixture-audio.wav', 'sha': hashlib.sha256(audio).hexdigest(), 'now': time.time()})
    repo.finish(job['id'], job['attempt_id'], 'COMPLETED')
    engine.dispose()
    uvicorn.run(create_app(settings), host='127.0.0.1', port=8877, access_log=False)

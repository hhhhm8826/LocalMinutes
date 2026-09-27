import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.youtube_jobs import register_youtube
from test_queue_media import context, wav_bytes  # noqa: F401


def test_video_file_kind_list_idempotency_and_global_fifo(context):  # noqa: F811
    settings, repo = context
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        csrf = client.post('/api/auth/local', headers={'origin': settings.origin}).json()['csrf_token']
        headers = {'origin': settings.origin, 'x-csrf-token': csrf, 'idempotency-key': 'video-create-001'}
        body = {'title': '영상 자료', 'speakers': 4, 'language': 'en', 'occurred_at': '2020-01-01T00:00:00Z'}
        video = client.post('/api/videos', json=body, headers=headers).json()
        assert video['document_kind'] == 'video_summary' and video['source_kind'] == 'file'
        options = json.loads(video['settings_json'])
        assert options['occurred_at'] is None and options['speakers'] is None
        assert options['language'] == 'auto' and options['allow_external_text']
        assert client.post('/api/videos', json=body, headers=headers).json()['id'] == video['id']
        assert client.post('/api/meetings', json=body, headers=headers).status_code == 409
        headers['idempotency-key'] = 'meeting-create-001'
        meeting = client.post('/api/meetings', json={'title': '회의 자료'}, headers=headers).json()
        assert [item['id'] for item in client.get('/api/meetings').json()] == [meeting['id']]
        assert [item['id'] for item in client.get('/api/meetings?document_kind=video_summary').json()] == [video['id']]
        jobs = []
        for item in (video, meeting):
            headers['idempotency-key'] = 'upload-' + item['id']
            response = client.put(f"/api/meetings/{item['id']}/media?filename=fixture.wav", content=wav_bytes(1), headers=headers)
            assert response.status_code == 200
            jobs.append(response.json())
        queue = client.get('/api/jobs').json()
        assert {item['document_kind'] for item in queue} == {'meeting', 'video_summary'}
        assert repo.claim()['meeting_id'] == video['id']
        assert repo.claim() is None
        assert {item['meeting_id']: item['queue_position'] for item in queue} == {video['id']: 1, meeting['id']: 2}


def test_global_queue_keeps_old_blocker_and_all_waiting_jobs(context):  # noqa: F811
    settings, repo = context
    jobs = [register_youtube(repo, 'https://www.youtube.com/watch?v=AbCde_123-4',
                             f'영상 {index}', f'queue-window-{index}') for index in range(205)]
    first = repo.claim()
    assert first['id'] == jobs[0]['id']
    assert repo.finish(first['id'], first['attempt_id'], 'BLOCKED', 'TEST_BLOCKER')
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        client.post('/api/auth/local', headers={'origin': settings.origin})
        queue = client.get('/api/jobs').json()
        assert len(queue) == 205
        assert next(row for row in queue if row['id'] == first['id'])['state'] == 'BLOCKED'
        assert {row['id']: row['queue_position'] for row in queue}[jobs[-1]['id']] == 204
        assert repo.claim() is None
        with repo.write() as connection:
            connection.execute(text('UPDATE meetings SET deleted_at=1 WHERE id=:id'),
                               {'id': first['meeting_id']})
        assert first['id'] not in {row['id'] for row in client.get('/api/jobs').json()}
        assert repo.claim()['id'] == jobs[1]['id']

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import text

from meeting_minutes.api import create_app
from meeting_minutes.contracts import GenerateMinutes, MeetingCreate
from meeting_minutes.library import export_minutes
from meeting_minutes.minutes_management import edit_minutes, queue_generation
from meeting_minutes.transcripts import edit_transcript
from test_minutes_management import complete, ready
from test_queue_media import context, wav_bytes  # noqa: F401


def test_search_current_content_and_literal_wildcards(context):  # noqa: F811
    _, repo = context
    meeting, _ = ready(context)
    assert [m['id'] for m in repo.meetings('점검')] == [meeting['id']]
    edit_transcript(repo, meeting['id'], meeting['revision'],
                    {'type': 'text', 'segment_id': 's1', 'text': '현재 100% 내용_'})
    assert repo.meetings('점검') == []
    assert len(repo.meetings('100%')) == 1
    assert repo.meetings('100_') == []
    assert len(repo.meetings('내용_')) == 1


def test_audio_range_requires_auth_and_omits_storage_keys(context):  # noqa: F811
    settings, repo = context
    meeting, _ = ready(context)
    job = repo.jobs()[0]
    data = wav_bytes()
    (settings.data_dir / 'artifacts' / 'test-audio.wav').write_bytes(data)
    with repo.write() as connection:
        connection.execute(text('INSERT INTO stage_artifacts VALUES (:id,:job,:stage,:attempt,:input,:path,:sha,:now)'),
            {'id': 'audio', 'job': job['id'], 'stage': 'EXTRACT', 'attempt': job['attempt_id'],
             'input': 'fixture', 'path': 'test-audio.wav', 'sha': 'fixture', 'now': time.time()})
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        path = f'/api/meetings/{meeting["id"]}'
        assert client.get(path + '/audio').status_code == 401
        client.post('/api/auth/login', headers={'origin': settings.origin}, json={'key': settings.owner_key_path.read_text()})
        response = client.get(path + '/audio', headers={'Range': 'bytes=10-19'})
        assert response.status_code == 206 and response.content == data[10:20]
        assert response.headers['content-range'] == f'bytes 10-19/{len(data)}'
        assert client.get(path + '/audio', headers={'Range': 'bytes=999999-'}).status_code == 416
        metadata = client.get(path + '/media').json()
        assert metadata['audio_available']
        assert 'stored_name' not in json.dumps(metadata) and 'test-audio.wav' not in json.dumps(metadata)
        (settings.data_dir / 'artifacts' / 'test-audio.wav').unlink()
        assert client.get(path + '/audio').status_code == 404
        assert not client.get(path + '/media').json()['audio_available']


def test_export_snapshot_and_markdown_escape(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    job = queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'export-fixture')
    original = complete(repo, job, version)
    from meeting_minutes.contracts import Minutes
    content = Minutes.model_validate(original['content'])
    content.summary = '새 요약 <script>alert(1)</script> [링크](https://example.com)'
    edit_minutes(repo, meeting['id'], original['id'], repo.meeting(meeting['id'])['revision'], content)
    assert '<script>' not in export_minutes(repo, meeting['id'])
    assert '\\[링크\\]' in export_minutes(repo, meeting['id'])
    assert '<script>' in export_minutes(repo, meeting['id'], markdown=False)
    old = export_minutes(repo, meeting['id'], original['id'])
    assert '새 요약' not in old and '점검 진행' in old and '근거 [' not in old
    assert len(repo.meetings('새 요약')) == 1
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        path = f'/api/meetings/{meeting["id"]}/export'
        assert client.get(path).status_code == 401
        client.post('/api/auth/login', headers={'origin': settings.origin}, json={'key': settings.owner_key_path.read_text()})
        response = client.get(path, params={'format': 'txt', 'version': original['id']})
        assert response.status_code == 200 and 'minutes_ver01_' in response.headers['content-disposition'] and '.txt' in response.headers['content-disposition']
        assert '새 요약' not in response.text
        assert client.get(path, params={'format': 'html'}).status_code == 422


def test_transcript_version_lookup_is_meeting_scoped_and_immutable(context):  # noqa: F811
    settings, repo = context
    meeting, original = ready(context)
    changed = edit_transcript(repo, meeting['id'], meeting['revision'],
                              {'type': 'text', 'segment_id': 's1', 'text': '편집된 현재 발언'})
    other = repo.create_meeting(MeetingCreate(title='다른 회의'))
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        path = f'/api/meetings/{meeting["id"]}/transcript'
        assert client.get(path + '/versions').status_code == 401
        client.post('/api/auth/login', headers={'origin': settings.origin}, json={'key': settings.owner_key_path.read_text()})
        assert {row['id'] for row in client.get(path + '/versions').json()} == {original, changed['id']}
        assert client.get(path, params={'version': original}).json()['content']['segments'][0]['text'] == '점검을 진행합시다.'
        assert client.get(path).json()['content']['segments'][0]['text'] == '편집된 현재 발언'
        assert client.get(f'/api/meetings/{other["id"]}/transcript', params={'version': original}).status_code == 404


def test_meeting_export_omits_playback_offsets_but_preserves_substantive_times(monkeypatch):
    import meeting_minutes.library as library
    value = {'document_kind':'meeting','status':'draft','revision':1,'metadata':{'title':'검토 회의'},
             'summary':'다음 회의는 10:30에 시작한다.', 'topics':[{'title':'출시 검토','text':'보안 점검 후 출시',
             'conclusion':'보류','starts':[{'start_ms':2000},{'start_ms':65000}]}],
             'decisions':[],'action_items':[],'open_questions':[],'review_notes':[]}
    monkeypatch.setattr(library,'read_minutes',lambda *args: {'document':value})
    for markdown in (True, False):
        output=library.export_minutes(None,'m',markdown=markdown)
        assert '00:02' not in output and '01:05' not in output
        assert '10:30' in output and '출시 검토' in output
    value.update(document_kind='video_summary',claims=[])
    value['claims'] = [{'text':'old duplicate claim','attribution':'speaker'}]
    for markdown in (True, False):
        output = library.export_minutes(None,'m',markdown=markdown)
        assert '00:02' not in output and '01:05' not in output
        assert '10:30' in output and '출시 검토' in output
        assert 'old duplicate claim' not in output


def test_export_literal_punctuation_and_windows_newlines(monkeypatch):
    import meeting_minutes.library as library
    body = "문장. '따옴표' \"인용\" & <tag> [링크](url) *강조* C:\\temp\\file\n다음 줄!"
    value = {'document_kind':'meeting','status':'draft','revision':1,'metadata':{'title':'회의'},
             'summary':body, 'topics':[], 'decisions':[], 'action_items':[], 'open_questions':[],
             'review_notes':["검토 '메모'."]}
    monkeypatch.setattr(library, 'read_minutes', lambda *args: {'document':value})
    for kind in ('meeting', 'video_summary'):
        value.update(document_kind=kind, claims=[])
        plain = library.export_minutes(None, 'm', markdown=False)
        md = library.export_minutes(None, 'm', markdown=True)
        assert body.replace('문장. ', '문장.\n').replace('\n','\r\n') in plain
        assert "문장.  \r\n'따옴표'" in md and "&\\#" not in md and '&#x27;' not in md
        assert '&lt;tag&gt;' in md and '\\[링크\\]' in md
        for result in (plain, md):
            assert '\r\n' in result and '\n' not in result.replace('\r\n','')


def test_export_range_tilde_and_selected_snapshot_filename(monkeypatch):
    from datetime import datetime
    import meeting_minutes.library as library
    document = {'document_kind':'meeting','status':'draft','revision':3,'metadata':{'title':'회의'},
        'summary':'편도 1시간 40분~2시간 통근. ~~취소~~', 'topics':[], 'decisions':[],
        'action_items':[], 'open_questions':[], 'review_notes':[], 'claims':[]}
    record = {'document':document,'created_at':datetime.fromisoformat('2026-09-26T15:30:00+00:00').timestamp()}
    reads = []
    def read(repo, meeting, version):
        reads.append(version)
        return record
    monkeypatch.setattr(library,'read_minutes',read)
    for kind,prefix in [('meeting','minutes'),('video_summary','summary')]:
        document['document_kind'] = kind
        for markdown,ext in [(True,'md'),(False,'txt')]:
            reads.clear()
            filename,body = library.export_download(None,'m','chosen-v3',markdown)
            assert reads == ['chosen-v3']
            assert filename == f'{prefix}_ver03_0927.{ext}'
            assert ('1시간 40분\\~2시간' if markdown else '1시간 40분~2시간') in body
            assert ('\\~\\~취소\\~\\~' if markdown else '~~취소~~') in body
            assert '\n' not in body.replace('\r\n','')

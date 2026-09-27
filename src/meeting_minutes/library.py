"""회의 자료 조회와 텍스트 내보내기. 저장 키는 응답에 노출하지 않는다."""
from datetime import datetime
from zoneinfo import ZoneInfo
import html
import json
import re

from sqlalchemy import text

from .files import safe_file
from .minutes_management import read_minutes
from .repository import Missing
from .prose import sentence_lines


def audio_path(repository, settings, meeting_id):
    repository.meeting(meeting_id)
    with repository.engine.connect() as connection:
        rows = connection.execute(text('''SELECT a.path FROM stage_artifacts a
            JOIN jobs j ON j.id=a.job_id WHERE j.meeting_id=:id AND a.stage='EXTRACT'
            ORDER BY a.created_at DESC'''), {'id': meeting_id}).mappings().all()
    for row in rows:
        path = safe_file(settings.data_dir / 'artifacts', row['path'])
        if path.is_file():
            return path
    raise Missing('AUDIO_NOT_AVAILABLE')


def media_info(repository, settings, meeting_id):
    meeting = repository.meeting(meeting_id)
    with repository.engine.connect() as connection:
        row = connection.execute(text('''SELECT original_name,size_bytes,duration_ms,tracks_json,selected_track
            FROM media_assets WHERE meeting_id=:id'''), {'id': meeting_id}).mappings().first()
    try:
        audio_path(repository, settings, meeting_id)
        available = True
    except Missing:
        available = False
    availability = {'audio_available': available, 'transcript_available': meeting['transcript_version'] is not None,
                    'media_expired': meeting['media_expired_at'] is not None,
                    'transcript_expired': meeting['transcript_expired_at'] is not None,
                    'result_available': meeting['minutes_revision'] is not None}
    if not row:
        return {**availability, 'media': None}
    return {**availability, 'media': {key: row[key] for key in
            ('original_name', 'size_bytes', 'duration_ms', 'selected_track')} |
            {'tracks': json.loads(row['tracks_json'] or '[]')}}


def export_minutes(repository, meeting_id, version=None, markdown=True):
    return render_export(read_minutes(repository, meeting_id, version), markdown)


def export_download(repository, meeting_id, version=None, markdown=True):
    # One snapshot supplies both filename and body even if current changes concurrently.
    record = read_minutes(repository, meeting_id, version)
    value = record['document']
    prefix = 'summary' if value['document_kind'] == 'video_summary' else 'minutes'
    date = datetime.fromtimestamp(record['created_at'], ZoneInfo('Asia/Seoul')).strftime('%m%d')
    extension = 'md' if markdown else 'txt'
    filename = f"{prefix}_ver{value['revision']:02d}_{date}.{extension}"
    return filename, render_export(record, markdown)


def render_export(record, markdown=True):
    value = record['document']

    def clean(value):
        value = str(value).replace('\r\n', '\n').replace('\r', '\n')
        if markdown:
            # Escape literal Markdown before HTML, so generated entities stay intact.
            value = re.sub(r'([\\`*_{}\[\]#+|~-])', r'\\\1', value)
            value = re.sub(r'(?m)^(\s*\d+)([.)])(?=\s)', r'\1\\\2', value)
            return html.escape(value, quote=False).replace('\n', '  \n')
        return value

    def prose(value):
        return clean(sentence_lines(str(value)))

    def heading(value, level=2):
        return ('#' * level + ' ' if markdown else '') + clean(value)

    video = value['document_kind'] == 'video_summary'
    status = '요약본' if video else ('초안' if value['revision'] == 1 else '회의록')
    lines = [heading(value['metadata']['title'], 1), '']
    if not video:
        lines.extend([f"버전 {value['revision']} · {status}", ''])
    lines.extend([heading('요약'), prose(value['summary']), ''])

    lines.append(heading('주요 주제' if video else '주요 논제'))
    for topic in value['topics']:
        if topic['title']:
            lines.append(heading(topic['title'], 3))
        lines.append(prose(topic['text']))
        if topic['conclusion']:
            lines.append(prose(topic['conclusion']))
        lines.append('')
    if video:
        return '\n'.join(lines).replace('\n', '\r\n')
    for key, label in [('decisions', '결정'), ('action_items', '할 일'), ('open_questions', '미결 사항')]:
        lines.append(heading(label))
        for item in value[key]:
            lines.append('- ' + prose(item.get('text', item.get('task', ''))).replace('\n', '\n  '))
            if key == 'action_items':
                owner = item.get('owner_name') or '미정'
                lines.append('  담당: ' + clean(owner) + ' / 기한: ' + clean(item['due_date'] or '미정'))
                if item['due_date_original_expression']:
                    lines.append('  원래 날짜 표현: ' + clean(item['due_date_original_expression']))
        if not value[key]:
            lines.append('없음')
        lines.append('')
    if value['review_notes']:
        lines.extend([heading('검토 메모'), *[prose(note) for note in value['review_notes']], ''])
    return '\n'.join(lines).replace('\n', '\r\n')

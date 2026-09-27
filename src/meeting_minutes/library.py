"""회의 자료 조회와 텍스트 내보내기. 저장 키는 응답에 노출하지 않는다."""
import html
import json
import re

from sqlalchemy import text

from .files import safe_file
from .minutes_management import read_minutes
from .repository import Missing


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
    repository.meeting(meeting_id)
    with repository.engine.connect() as connection:
        row = connection.execute(text('''SELECT original_name,size_bytes,duration_ms,tracks_json,selected_track
            FROM media_assets WHERE meeting_id=:id'''), {'id': meeting_id}).mappings().first()
    try:
        audio_path(repository, settings, meeting_id)
        available = True
    except Missing:
        available = False
    if not row:
        return {'audio_available': available, 'media': None}
    return {'audio_available': available, 'media': {key: row[key] for key in
            ('original_name', 'size_bytes', 'duration_ms', 'selected_track')} |
            {'tracks': json.loads(row['tracks_json'] or '[]')}}


def export_minutes(repository, meeting_id, version=None, markdown=True):
    meeting = repository.meeting(meeting_id)
    record = read_minutes(repository, meeting_id, version)
    value = record['content']
    with repository.engine.connect() as connection:
        transcript = json.loads(connection.execute(text('''SELECT content_json FROM transcript_versions
            WHERE id=:id AND meeting_id=:meeting'''),
            {'id': value['transcript_version'], 'meeting': meeting_id}).scalar_one())
    segments = {item['id']: item for item in transcript['segments']}
    speakers = transcript['speakers']

    def clean(value):
        value = str(value)
        if markdown:
            return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(value)).replace('\n', '  \n')
        return value

    def heading(value, level=2):
        return ('#' * level + ' ' if markdown else '') + clean(value)

    lines = [heading(meeting['title'], 1), '',
             f"버전 {value['revision']} · {'확정' if value['status'] == 'confirmed' else '초안'}", '',
             heading('요약'), clean(value['summary']), '']
    for key, label in [('topics', '주요 논의'), ('decisions', '결정'), ('action_items', '할 일'), ('open_questions', '미결 질문')]:
        lines.append(heading(label))
        for item in value[key]:
            lines.append('- ' + clean(item.get('text', item.get('task', ''))))
            lines.append('  검토: ' + {'needs_review': '검토 필요', 'verified': '확인함', 'user_authored': '사용자 작성'}[item['review_status']])
            if key == 'action_items':
                owner = speakers.get(item['owner_speaker_id'], {}).get('name', '미정')
                lines.append('  담당: ' + clean(owner) + ' / 기한: ' + clean(item['due_date'] or '미정'))
                if item['due_date_original_expression']:
                    lines.append('  원래 날짜 표현: ' + clean(item['due_date_original_expression']))
            for source in item['source_segment_ids']:
                segment = segments[source]
                seconds = segment['start_ms'] // 1000
                lines.append(f'  근거 [{seconds // 60:02d}:{seconds % 60:02d}] ' + clean(segment['text']))
        if not value[key]:
            lines.append('없음')
        lines.append('')
    if value['review_notes']:
        lines.extend([heading('검토 메모'), *[clean(note) for note in value['review_notes']], ''])
    return '\n'.join(lines)

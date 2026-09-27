"""화자 연결과 불변 편집 버전. 모델 실행과 독립적인 계약이다."""
import copy
import json
import math
import time

from sqlalchemy import text

from .repository import Conflict, Missing, identifier
from .utterances import derive_utterances


def intersection(a, b, c, d):
    return max(0, min(b, d) - max(a, c))


def assign_speaker(start_ms, end_ms, exclusive, regular):
    overlaps = {}
    for turn in exclusive:
        length = intersection(start_ms, end_ms, turn['start_ms'], turn['end_ms'])
        overlaps[turn['speaker_id']] = overlaps.get(turn['speaker_id'], 0) + length
    ranked = sorted(overlaps.items(), key=lambda item: item[1], reverse=True)
    # 실제 regular 화자 구간끼리 겹치는 구간만 overlap으로 표시한다.
    active = [turn for turn in regular if intersection(start_ms, end_ms, turn['start_ms'], turn['end_ms']) > 0]
    simultaneous = any(left['speaker_id'] != right['speaker_id']
                       and intersection(max(start_ms, left['start_ms']), min(end_ms, left['end_ms']),
                                        right['start_ms'], right['end_ms']) > 0
                       for i, left in enumerate(active) for right in active[i+1:])
    if not ranked or ranked[0][1] <= 0 or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None, True, simultaneous
    coverage = ranked[0][1] / max(1, end_ms - start_ms)
    uncertain = simultaneous or end_ms - start_ms < 150 or coverage < .5
    return ranked[0][0], uncertain, simultaneous


def attribute_segments(aligned, exclusive, regular):
    output = []
    for segment in aligned:
        # 시간 정보가 없는 단어도 버리지 않고 원 구간 시각으로 대체한다.
        words = segment.get('words') or [{'word': segment['text']}]
        for word in words:
            text_value = word.get('word', '')
            if not text_value:
                continue
            timed = word.get('start') is not None and word.get('end') is not None
            start = round((word['start'] if timed else segment['start']) * 1000)
            end = round((word['end'] if timed else segment['end']) * 1000)
            speaker, uncertain, overlap = assign_speaker(start, end, exclusive, regular)
            item = {'id': f'seg_{len(output)+1:06d}', 'start_ms': start, 'end_ms': max(start, end),
                    'text': text_value, 'speaker_id': speaker,
                    'needs_review': uncertain or not timed, 'overlap': overlap,
                    'timing': 'word' if timed else 'segment',
                    'attribution': {'method': 'exclusive_time_overlap', 'short': end - start < 150,
                                    'ambiguous': uncertain, 'alignment_fallback': not timed,
                                    'source_start_ms': round(segment['start'] * 1000),
                                    'source_end_ms': round(segment['end'] * 1000)}}
            output.append(item)
    return output


def merge_candidates(speakers, regular, embeddings):
    representatives = {}
    for speaker in speakers:
        clean = [turn for turn in regular if turn['speaker_id'] == speaker
                 and turn['end_ms'] - turn['start_ms'] >= 1000
                 and not any(other['speaker_id'] != speaker and intersection(
                     turn['start_ms'], turn['end_ms'], other['start_ms'], other['end_ms']) > 0 for other in regular)]
        representatives[speaker] = sorted(clean, key=lambda turn: turn['end_ms'] - turn['start_ms'], reverse=True)[:3]
    candidates = []
    for i, left in enumerate(speakers):
        for right in speakers[i+1:]:
            a, b = embeddings.get(left), embeddings.get(right)
            if a is None or b is None or len(a) != len(b) or not a:
                continue
            if any(len(representatives[speaker]) < 2 or sum(t['end_ms'] - t['start_ms'] for t in representatives[speaker]) < 3000
                   for speaker in (left, right)):
                continue
            denominator = math.sqrt(sum(value*value for value in a) * sum(value*value for value in b))
            if not denominator:
                continue
            score = sum(x*y for x, y in zip(a, b)) / denominator
            if not math.isfinite(score):
                continue
            candidates.append({'left': left, 'right': right, 'similarity': max(-1, min(1, score)),
                               'representatives': {left: representatives[left], right: representatives[right]},
                               'automatic': False})
    return sorted(candidates, key=lambda item: item['similarity'], reverse=True)[:10]


def current_transcript(repository, meeting_id, version=None):
    meeting = repository.meeting(meeting_id)
    if not version and not meeting['transcript_version']:
        raise Missing('TRANSCRIPT_NOT_READY')
    with repository.engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM transcript_versions WHERE id=:id AND meeting_id=:meeting'),
                                 {'id': version or meeting['transcript_version'], 'meeting': meeting_id}).mappings().first()
    if not row:
        raise Missing('TRANSCRIPT_NOT_FOUND')
    content = json.loads(row['content_json'])
    try:
        reading = derive_utterances(content, row['id'])
        reading_error = None
    except ValueError as exc:
        reading, reading_error = None, str(exc)
    return {'id': row['id'], 'meeting_revision': meeting['revision'], 'kind': row['kind'],
            'parent_id': row['parent_id'], 'content': content,
            'reading': reading, 'reading_error': reading_error}


def list_transcripts(repository, meeting_id):
    repository.meeting(meeting_id)
    with repository.engine.connect() as connection:
        return [dict(row) for row in connection.execute(text('''SELECT id,parent_id,kind,created_at
            FROM transcript_versions WHERE meeting_id=:id ORDER BY created_at DESC,id'''),
            {'id': meeting_id}).mappings()]


def edit_transcript(repository, meeting_id, expected_revision, operation):
    with repository.write() as connection:
        meeting = connection.execute(text('SELECT * FROM meetings WHERE id=:id AND deleted_at IS NULL'),
                                     {'id': meeting_id}).mappings().first()
        if not meeting or not meeting['transcript_version']:
            raise Missing('TRANSCRIPT_NOT_READY')
        if meeting['revision'] != expected_revision:
            raise Conflict('REVISION_CONFLICT')
        previous = connection.execute(text('SELECT * FROM transcript_versions WHERE id=:id'),
                                      {'id': meeting['transcript_version']}).mappings().one()
        content = json.loads(previous['content_json'])
        action = operation['type']
        if action == 'undo':
            undo_version = content.get('undo_version_id', previous['parent_id'])
            if not undo_version:
                raise Conflict('NOTHING_TO_UNDO')
            parent = connection.execute(text('SELECT * FROM transcript_versions WHERE id=:id'),
                                        {'id': undo_version}).mappings().one()
            content = json.loads(parent['content_json'])
            content['undo_version_id'] = content.get('undo_version_id', parent['parent_id'])
        elif action == 'utterance_text':
            reading = derive_utterances(content, previous['id'])
            selected = next((u for u in reading['utterances'] if u['id'] == operation['utterance_id']), None)
            if selected is None:
                raise Conflict('INVALID_UTTERANCE')
            if not isinstance(operation['text'], str) or len(operation['text']) > 20000:
                raise Conflict('INVALID_TEXT')
            edits = [e for e in content.get('utterance_edits', []) if e['source_ids'] != selected['source_ids']]
            content['utterance_edits'] = edits + [{'source_ids': selected['source_ids'], 'text': operation['text']}]
        elif action == 'rename':
            speaker = operation['speaker_id']
            name = operation['name'].strip()
            if speaker not in content['speakers'] or not 1 <= len(name) <= 80:
                raise Conflict('INVALID_SPEAKER_NAME')
            content['speakers'][speaker]['name'] = name
            content['speakers'][speaker]['user_verified'] = True
        elif action in {'reassign', 'merge'}:
            target = operation['speaker_id']
            if target is not None and target not in content['speakers']:
                raise Conflict('INVALID_SPEAKER')
            if action == 'merge':
                source = operation['source_speaker_id']
                if target is None or source == target or source not in content['speakers']:
                    raise Conflict('INVALID_MERGE')
                selection = [segment['id'] for segment in content['segments'] if segment['speaker_id'] == source]
                del content['speakers'][source]
                content['merge_candidates'] = [candidate for candidate in content.get('merge_candidates', [])
                                               if source not in (candidate['left'], candidate['right'])]
            else:
                selection = operation['segment_ids']
                if not selection or not set(selection) <= {segment['id'] for segment in content['segments']}:
                    raise Conflict('INVALID_SEGMENTS')
            for segment in content['segments']:
                if segment['id'] in selection:
                    segment['speaker_id'] = target
                    segment['user_edited'] = True
                    segment['needs_review'] = target is None
        elif action == 'text':
            found = False
            for segment in content['segments']:
                if segment['id'] == operation['segment_id']:
                    if not isinstance(operation['text'], str) or len(operation['text']) > 20000:
                        raise Conflict('INVALID_TEXT')
                    segment['text'] = operation['text']
                    segment['user_edited'] = True
                    found = True
            if not found:
                raise Conflict('INVALID_SEGMENT')
        else:
            raise Conflict('UNKNOWN_EDIT')
        new_id = identifier()
        if action != 'undo':
            content['undo_version_id'] = previous['id']
        content['last_edit'] = copy.deepcopy(operation)
        connection.execute(text('''INSERT INTO transcript_versions(id,meeting_id,parent_id,kind,content_json,created_at)
            VALUES (:id,:meeting,:parent,'user',:content,:now)'''),
            {'id': new_id, 'meeting': meeting_id, 'parent': previous['id'],
             'content': json.dumps(content, ensure_ascii=False), 'now': time.time()})
        connection.execute(text('''UPDATE meetings SET transcript_version=:version,revision=revision+1,
            updated_at=:now WHERE id=:id'''), {'version': new_id, 'now': time.time(), 'id': meeting_id})
    return current_transcript(repository, meeting_id)

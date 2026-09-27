"""Deterministic reading layer over immutable word/segment snapshots.

This layer never changes speaker assignments or invokes an acoustic model.
Original alignment text supplies whitespace only when its non-space characters
exactly match the stored words; historical text edits therefore remain intact.
"""
import hashlib
import re

RULE_VERSION = 'utterances-2'


def _pieces(content):
    segments = content.get('segments', [])
    aligned = content.get('alignment', {}).get('segments', [])
    sources = {(round(s['start'] * 1000), round(s['end'] * 1000)): s['text'] for s in aligned}
    result = [s['text'] for s in segments]
    cursor = 0
    while cursor < len(segments):
        attribution = segments[cursor].get('attribution', {})
        key = (attribution.get('source_start_ms'), attribution.get('source_end_ms'))
        stop = cursor + 1
        while stop < len(segments):
            other = segments[stop].get('attribution', {})
            if (other.get('source_start_ms'), other.get('source_end_ms')) != key:
                break
            stop += 1
        original = sources.get(key)
        words = [re.sub(r'\s', '', s['text']) for s in segments[cursor:stop]]
        if original is not None and all(words) and ''.join(words) == re.sub(r'\s', '', original):
            positions = [i for i, char in enumerate(original) if not char.isspace()]
            used, start = 0, 0
            for index, word in enumerate(words):
                used += len(word)
                end = positions[used] if used < len(positions) else len(original)
                result[cursor + index] = original[start:end]
                start = end
        else:
            # A legacy snapshot without source spacing retains each exact token.
            # Add a separator only when neither token already carries one.
            for index in range(cursor, stop - 1):
                left, right = result[index], result[index + 1]
                if left and right and not left[-1].isspace() and not right[0].isspace():
                    if right[0] not in ',.!?;:)]}、。！？':
                        result[index] += ' '
        cursor = stop
    return result


def derive_utterances(content, transcript_version):
    """Return source-linked utterances, without mutating the input snapshot."""
    segments = content.get('segments', [])
    if any('start_ms' not in s or 'end_ms' not in s for s in segments):
        raise ValueError('TRANSCRIPT_TIMES_UNAVAILABLE')
    pieces = _pieces(content)
    edits = content.get('utterance_edits', [])
    edit_owners = {source: n for n, edit in enumerate(edits) for source in edit['source_ids']}
    groups, current = [], []
    for index, segment in enumerate(segments):
        if segment['start_ms'] < 0 or segment['end_ms'] < segment['start_ms']:
            raise ValueError('INVALID_TRANSCRIPT_TIME')
        if current:
            previous = segments[current[-1]]
            duration = segment['end_ms'] - segments[current[0]]['start_ms']
            source_start = segment.get('attribution', {}).get('source_start_ms')
            previous_source = previous.get('attribution', {}).get('source_start_ms')
            boundary = (
                segment.get('speaker_id') != previous.get('speaker_id')
                or bool(segment.get('overlap')) != bool(previous.get('overlap'))
                or segment['start_ms'] - previous['end_ms'] >= 1000
                or (source_start is not None and source_start != previous_source)
                or re.search(r'[.!?。！？][\"\'”’)]*\s*$', pieces[current[-1]])
                or (duration > 20000 and (
                    pieces[current[-1]][-1:].isspace()
                    or pieces[current[-1]].rstrip().endswith((',', ';', ':', '，'))))
            )
            owner, previous_owner = edit_owners.get(segment['id']), edit_owners.get(previous['id'])
            if owner is not None or previous_owner is not None:
                boundary = owner != previous_owner
            if boundary:
                groups.append(current)
                current = []
        current.append(index)
    if current:
        groups.append(current)
    utterances = []
    for group in groups:
        source = [segments[i] for i in group]
        ids = [s['id'] for s in source]
        correction = next((edit['text'] for edit in edits if edit['source_ids'] == ids), None)
        identity = '\0'.join([RULE_VERSION, transcript_version, *ids])
        reasons = set()
        for s in source:
            attr = s.get('attribution', {})
            if s.get('overlap'):
                reasons.add('overlap')
            if s.get('speaker_id') is None:
                reasons.add('unknown_speaker')
            if attr.get('alignment_fallback') or s.get('timing') == 'segment':
                reasons.add('alignment_fallback')
            # The legacy ambiguous flag includes short words. Do not promote
            # that duration-only warning to an entire multiword utterance.
            if attr.get('ambiguous') and not attr.get('short'):
                reasons.add('speaker_attribution')
        start = min(s['start_ms'] for s in source)
        end = max(s['end_ms'] for s in source)
        if end - start < 150:
            reasons.add('short_utterance')
        utterances.append({
            'id': 'utt_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
            'start_ms': start, 'end_ms': end,
            'text': correction if correction is not None else ''.join(pieces[i] for i in group).strip(),
            'speaker_id': source[0].get('speaker_id'), 'source_ids': ids,
            'uncertainty': sorted(reasons), 'needs_review': bool(reasons),
            'user_edited': correction is not None,
        })
    return {'rule_version': RULE_VERSION, 'transcript_version': transcript_version,
            'utterances': utterances}

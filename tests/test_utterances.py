import copy

import pytest

from meeting_minutes.transcripts import attribute_segments
from meeting_minutes.utterances import derive_utterances


def snapshot(words, text, exclusive=None, regular=None):
    aligned = [{'start': 0, 'end': 5, 'text': text, 'words': words}]
    turns = exclusive or [{'start_ms': 0, 'end_ms': 5000, 'speaker_id': 'A'}]
    return {'alignment': {'segments': aligned},
            'segments': attribute_segments(aligned, turns, regular or turns)}


def test_original_mixed_spacing_and_deterministic_source_coverage():
    content = snapshot([
        {'word': 'API', 'start': 0, 'end': .1},
        {'word': '를', 'start': .1, 'end': .2},
        {'word': '확인합니다.', 'start': .2, 'end': 1},
        {'word': 'Next', 'start': 1.1, 'end': 1.6},
        {'word': 'step!', 'start': 1.6, 'end': 2},
    ], 'API를 확인합니다. Next step!')
    before = copy.deepcopy(content)
    reading = derive_utterances(content, 'v1')
    assert reading == derive_utterances(content, 'v1')
    assert content == before
    utterances = reading['utterances']
    assert [u['text'] for u in utterances] == ['API를 확인합니다.', 'Next step!']
    assert [i for u in utterances for i in u['source_ids']] == [s['id'] for s in content['segments']]
    assert not utterances[0]['needs_review']
    assert derive_utterances(content, 'v2')['utterances'][0]['id'] != utterances[0]['id']


def test_speaker_overlap_and_fallback_are_not_merged_or_retimed():
    turns = [{'start_ms': 0, 'end_ms': 1000, 'speaker_id': 'A'},
             {'start_ms': 1000, 'end_ms': 5000, 'speaker_id': 'B'}]
    regular = turns + [{'start_ms': 1500, 'end_ms': 2500, 'speaker_id': 'A'}]
    content = snapshot([{'word': 'hello', 'start': 0, 'end': .8},
                        {'word': 'yes', 'start': 1.1, 'end': 1.4},
                        {'word': 'both', 'start': 1.6, 'end': 2},
                        {'word': 'missing'}], 'hello yes both missing', turns, regular)
    result = derive_utterances(content, 'v')['utterances']
    assert [u['speaker_id'] for u in result[:3]] == ['A', 'B', 'B']
    assert 'overlap' in result[2]['uncertainty']
    assert 'alignment_fallback' in result[-1]['uncertainty']
    assert result[-1]['start_ms'] == 0 and result[-1]['end_ms'] == 5000
    assert sum(len(u['source_ids']) for u in result) == 4


def test_historical_edit_is_not_replaced_by_asr_text():
    content = snapshot([{'word': '원문', 'start': 0, 'end': 1}], '원문')
    content['segments'][0]['text'] = '교정된 내용'
    assert derive_utterances(content, 'edited')['utterances'][0]['text'] == '교정된 내용'
    content['segments'][0].pop('start_ms')
    with pytest.raises(ValueError, match='TIMES_UNAVAILABLE'):
        derive_utterances(content, 'edited')


def test_source_sentence_boundary_and_long_unpunctuated_speech():
    aligned = [
        {'start': 0, 'end': 2, 'text': '문장 하나', 'words': []},
        {'start': 2, 'end': 4, 'text': 'next sentence', 'words': []},
    ]
    turns = [{'start_ms': 0, 'end_ms': 50000, 'speaker_id': 'A'}]
    content = {'alignment': {'segments': aligned},
               'segments': attribute_segments(aligned, turns, turns)}
    assert [u['text'] for u in derive_utterances(content, 'v')['utterances']] == ['문장 하나', 'next sentence']
    words = [{'word': f'w{i}', 'start': i, 'end': i + .9} for i in range(45)]
    aligned = [{'start': 0, 'end': 45, 'text': ' '.join(w['word'] for w in words), 'words': words}]
    content = {'alignment': {'segments': aligned},
               'segments': attribute_segments(aligned, turns, turns)}
    utterances = derive_utterances(content, 'v')['utterances']
    assert len(utterances) == 3
    assert ' '.join(u['text'] for u in utterances) == aligned[0]['text']

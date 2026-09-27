import json
from pathlib import Path

import pytest

from meeting_minutes.prose import sentence_lines
from meeting_minutes.library import render_export


@pytest.mark.parametrize('source,expected', json.loads((Path(__file__).parent / 'fixtures/sentence-lines.json').read_text()))
def test_sentence_boundaries(source, expected):
    assert sentence_lines(source) == expected
    assert sentence_lines(expected) == expected


@pytest.mark.parametrize('kind', ['meeting', 'video_summary'])
@pytest.mark.parametrize('markdown', [False, True])
def test_export_sentence_lines_without_mutation(kind, markdown):
    prose = '첫 문장이다. 다음 문장이다.'
    document = dict(document_kind=kind, revision=1, metadata={'title':'제목. 유지'}, summary=prose,
        topics=[dict(title='주제. 유지', text=prose, conclusion=prose)],
        decisions=[{'text':prose}], action_items=[], open_questions=[], review_notes=[prose])
    before = json.dumps(document)
    output = render_export({'document':document}, markdown)
    assert ('첫 문장이다.  \r\n다음 문장이다.' if markdown else '첫 문장이다.\r\n다음 문장이다.') in output
    assert '제목. 유지' in output and '주제. 유지' in output
    assert json.dumps(document) == before
    assert '\n' not in output.replace('\r\n','')
    if kind == 'meeting':
        assert ('- 첫 문장이다.  \r\n  다음 문장이다.' if markdown else '- 첫 문장이다.\r\n  다음 문장이다.') in output

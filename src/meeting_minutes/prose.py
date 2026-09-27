"""Sentence line breaks for display/export only; never rewrite stored documents."""
import re


_BOUNDARY = re.compile(r"\.([\"'”’」』)\]]*)(?:[ \t]+|(?=[가-힣]))")
_ABBREVIATIONS = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'vs', 'etc', 'e.g', 'i.e'}


def sentence_lines(value):
    value = value.replace('\r\n', '\n').replace('\r', '\n')

    def boundary(match):
        token = re.search(r'[^\s]+$', value[:match.start()])
        token = token.group() if token else ''
        if (token.lower() in _ABBREVIATIONS or re.fullmatch(r'(?:[A-Za-z]\.)*[A-Za-z]', token)
                or re.fullmatch(r'[0-9.]+', token) or token.endswith('.')):
            return match.group()
        # A URL/email without whitespace can contain Korean domain/path text.
        if not match.group().endswith((' ', '\t')) and ('://' in token or '@' in token or token.startswith('www.')):
            return match.group()
        return '.' + match.group(1) + ('' if value[match.end():].startswith('\n') else '\n')

    return _BOUNDARY.sub(boundary, value)

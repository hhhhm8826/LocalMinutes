import json

import pytest
from pydantic import ValidationError

from meeting_minutes.contracts import MeetingCreate
from meeting_minutes.media import probe
from meeting_minutes.processes import ProcessFailure
from meeting_minutes.settings import Settings


def test_exact_120_minute_metadata_allowed_and_any_excess_rejected(monkeypatch, tmp_path):
    metadata = {'streams': [{'index': 0, 'codec_type': 'audio'}], 'format': {'duration': '7200'}}
    monkeypatch.setattr('meeting_minutes.media.bounded_run', lambda *a, **k: json.dumps(metadata))
    assert probe(tmp_path / 'fixture.wav')[1] == 7_200_000
    metadata['format']['duration'] = '7200.0001'
    with pytest.raises(ProcessFailure):
        probe(tmp_path / 'fixture.wav')


def test_release_limits_cannot_be_configured_above_declared_boundaries():
    assert MeetingCreate(title='Boundary', speakers=12).speakers == 12
    assert Settings().max_upload_bytes == 2_000_000_000
    assert Settings().max_duration_seconds == 7200
    for value in (0, 13):
        with pytest.raises(ValidationError):
            MeetingCreate(title='Boundary', speakers=value)
    for values in ({'max_upload_bytes': 2_000_000_001}, {'max_duration_seconds': 7201}):
        with pytest.raises(ValidationError):
            Settings(**values)

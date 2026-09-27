import pytest

from meeting_minutes.processes import ProcessFailure
from meeting_minutes.youtube_policy import canonical_url, checked_metadata

ID = 'AbCde_123-4'
URL = f'https://www.youtube.com/watch?v={ID}'


@pytest.mark.parametrize('url', [URL + '&list=playlist&t=50', f'https://youtu.be/{ID}?si=foo',
                               f'https://m.youtube.com/shorts/{ID}', f'https://youtube.com/live/{ID}?start=90'])
def test_canonical_single_video_ignores_offsets(url):
    assert canonical_url(url) == URL


@pytest.mark.parametrize('url', ['http://youtube.com/watch?v=' + ID, 'https://youtube.com.evil/watch?v=' + ID,
    'https://evil.youtube.com/watch?v=' + ID, 'https://user@youtube.com/watch?v=' + ID,
    'https://youtube.com:444/watch?v=' + ID, 'https://127.0.0.1/watch?v=' + ID,
    'https://youtube.com/playlist?list=x', 'https://youtube.com/@channel', URL + '&v=duplicate',
    'https://youtu.be/' + ID + '/extra', 'https://youtube.com/\nwatch?v=' + ID])
def test_reject_ambiguous_or_non_video_url(url):
    with pytest.raises(ProcessFailure, match='YOUTUBE_URL_INVALID'):
        canonical_url(url)


def metadata():
    return {'id': ID, 'extractor_key': 'Youtube', 'availability': 'public', 'duration': 180,
            'live_status': 'was_live', 'title': 'Untrusted title', 'upload_date': '20260102',
            'formats': [{'vcodec': 'none', 'acodec': 'opus'}]}


def test_finished_live_unknown_size_and_unknown_date():
    info = metadata()
    assert checked_metadata(info, URL)['published_at'] == '2026-01-02'
    info['upload_date'] = 'unknown'
    assert checked_metadata(info, URL)['published_at'] is None


@pytest.mark.parametrize('change,code', [({'_type': 'playlist'}, 'YOUTUBE_NOT_SINGLE_VIDEO'),
    ({'id': 'different'}, 'YOUTUBE_NOT_SINGLE_VIDEO'), ({'is_live': True}, 'YOUTUBE_LIVE_UNSUPPORTED'),
    ({'live_status': 'is_upcoming'}, 'YOUTUBE_LIVE_UNSUPPORTED'),
    ({'availability': 'needs_auth'}, 'YOUTUBE_ACCESS_RESTRICTED'), ({'has_drm': True}, 'YOUTUBE_ACCESS_RESTRICTED'),
    ({'duration': None}, 'YOUTUBE_DURATION_UNKNOWN'), ({'duration': float('nan')}, 'YOUTUBE_DURATION_UNKNOWN'),
    ({'duration': 7201}, 'DURATION_LIMIT'), ({'formats': []}, 'YOUTUBE_AUDIO_UNAVAILABLE'),
    ({'formats': [{'vcodec': 'none', 'acodec': 'opus', 'filesize': 2_000_000_001}]}, 'FILE_SIZE_LIMIT')])
def test_metadata_restrictions(change, code):
    with pytest.raises(ProcessFailure, match=code):
        checked_metadata(metadata() | change, URL)

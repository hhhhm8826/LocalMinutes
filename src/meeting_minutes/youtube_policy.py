"""Pure input and extractor-result checks; network enforcement is separate."""
import math
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from .processes import ProcessFailure

VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')
INPUT_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}


def canonical_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ProcessFailure('YOUTUBE_URL_INVALID')
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname not in INPUT_HOSTS or url.username is not None
                or url.password is not None or url.port not in (None, 443) or '\\' in value):
            raise ValueError
        if url.hostname == 'youtu.be':
            candidate = url.path[1:]
        elif url.path == '/watch':
            values = parse_qs(url.query, keep_blank_values=True).get('v', [])
            if len(values) != 1:
                raise ValueError
            candidate = values[0]
        else:
            parts = url.path.split('/')
            if len(parts) != 3 or parts[1] not in {'shorts', 'live'}:
                raise ValueError
            candidate = parts[2]
        if not VIDEO_ID.fullmatch(candidate):
            raise ValueError
    except ValueError as exc:
        raise ProcessFailure('YOUTUBE_URL_INVALID') from exc
    return f'https://www.youtube.com/watch?v={candidate}'


def checked_metadata(info, expected_url, *, max_seconds=7200, max_bytes=2_000_000_000):
    """Reject unsupported sources before downloading; never trust title as a path."""
    expected_id = canonical_url(expected_url).rsplit('=', 1)[1]
    if (info.get('_type', 'video') != 'video' or info.get('id') != expected_id
            or info.get('extractor_key') != 'Youtube' or info.get('entries') is not None):
        raise ProcessFailure('YOUTUBE_NOT_SINGLE_VIDEO')
    if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming', 'post_live'}:
        raise ProcessFailure('YOUTUBE_LIVE_UNSUPPORTED')
    if info.get('availability') not in {'public', 'unlisted'} or info.get('has_drm'):
        raise ProcessFailure('YOUTUBE_ACCESS_RESTRICTED')
    duration = info.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ProcessFailure('YOUTUBE_DURATION_UNKNOWN')
    if duration > max_seconds:
        raise ProcessFailure('DURATION_LIMIT')
    audio = [item for item in info.get('formats', []) if item.get('vcodec') == 'none'
             and item.get('acodec') not in (None, 'none') and not item.get('has_drm')]
    if not audio:
        raise ProcessFailure('YOUTUBE_AUDIO_UNAVAILABLE')
    # Unknown sizes are permitted only with the acquisition layer's byte budget.
    if all(isinstance(item.get('filesize'), (int, float)) and item['filesize'] > max_bytes for item in audio):
        raise ProcessFailure('FILE_SIZE_LIMIT')
    published = info.get('upload_date')
    try:
        published = datetime.strptime(published, '%Y%m%d').date().isoformat() if published else None
    except (ValueError, TypeError):
        published = None
    return {'source_url': canonical_url(expected_url), 'video_id': expected_id,
            'title': str(info.get('title') or expected_id)[:200],
            'channel': str(info['channel'])[:300] if info.get('channel') else None,
            'published_at': published, 'duration_seconds': duration}

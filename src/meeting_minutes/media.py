"""네트워크·플레이리스트를 허용하지 않는 FFmpeg 어댑터."""
import json
import math
import wave

from .processes import ProcessFailure, bounded_run


INPUT_POLICY = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,mov,matroska,webm,ogg,flac']


def probe(path, duration_limit=7200):
    raw = bounded_run(['ffprobe', '-v', 'error', *INPUT_POLICY, '-show_format', '-show_streams',
                       '-of', 'json', str(path)], timeout=30)
    try:
        value = json.loads(raw)
        tracks = [{'index': item['index'], 'codec': item.get('codec_name'),
                   'language': item.get('tags', {}).get('language'),
                   'default': bool(item.get('disposition', {}).get('default'))}
                  for item in value.get('streams', []) if item.get('codec_type') == 'audio']
        duration = float(value.get('format', {}).get('duration', 0))
    except (ValueError, KeyError, TypeError) as exc:
        raise ProcessFailure('INVALID_MEDIA_METADATA') from exc
    if not tracks:
        raise ProcessFailure('NO_AUDIO_TRACK')
    if not math.isfinite(duration) or duration < 0:
        raise ProcessFailure('INVALID_MEDIA_DURATION')
    if duration > duration_limit:
        raise ProcessFailure('MEDIA_TOO_LONG')
    return tracks, round(duration * 1000) if duration else None


def extract(path, output, track, duration_limit=7200):
    bounded_run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', *INPUT_POLICY,
                 '-i', str(path), '-map', f'0:{track}', '-vn', '-sn', '-dn', '-ac', '1', '-ar', '16000',
                 '-t', str(duration_limit + 1), '-c:a', 'pcm_s16le', '-f', 'wav', '-n', str(output)],
                timeout=1800, file_limit=(duration_limit + 2) * 32000 + 65536)
    try:
        with wave.open(str(output), 'rb') as audio:
            duration_ms = round(audio.getnframes() * 1000 / audio.getframerate())
            if audio.getnframes() > duration_limit * audio.getframerate():
                raise ProcessFailure('MEDIA_TOO_LONG')
            if audio.getnframes() == 0:
                raise ProcessFailure('EMPTY_AUDIO')
    except wave.Error as exc:
        raise ProcessFailure('INVALID_EXTRACTED_AUDIO') from exc
    return duration_ms

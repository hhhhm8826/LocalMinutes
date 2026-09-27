from pathlib import Path

import pytest
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import NoSupportingHandlers

from meeting_minutes.processes import ProcessFailure
from meeting_minutes.youtube_acquisition import RestrictedYoutubeDL, acquire
from meeting_minutes.youtube_transport import TransferBudget
from test_youtube_policy import metadata, URL


def test_director_has_only_restricted_transport():
    with RestrictedYoutubeDL({'quiet': True, 'proxy': '', 'cachedir': False}, TransferBudget()) as downloader:
        assert set(downloader._request_director.handlers) == {'Restricted'}
        assert list(downloader._ies) == ['Youtube']


def test_unexpected_transport_failure_uses_compatible_logger(monkeypatch):
    with RestrictedYoutubeDL({'quiet': True, 'proxy': '', 'cachedir': False}, TransferBudget()) as downloader:
        director = downloader._request_director
        def broken(request):
            raise ValueError('fixture failure')
        monkeypatch.setattr(director.handlers['Restricted'], 'send', broken)
        with pytest.raises(NoSupportingHandlers):
            director.send(Request(URL))
        assert set(director.handlers) == {'Restricted'}


def test_metadata_then_audio_and_hash_only_after_complete(tmp_path, monkeypatch):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    deno = tmp_path / 'deno'
    deno.touch()
    events = []
    info = metadata() | {'protocol': 'https', 'vcodec': 'none', 'acodec': 'opus'}
    def extract(self, url, download):
        assert not download and url == URL
        assert self.params['format'] == 'bestaudio[protocol=https][vcodec=none]'
        assert not self.params['remote_components']
        return info
    def process(self, value):
        assert value is info
        Path(self.params['outtmpl']['default']).write_bytes(b'fixture-audio')
    monkeypatch.setattr(RestrictedYoutubeDL, 'extract_info', extract)
    monkeypatch.setattr(RestrictedYoutubeDL, 'process_info', process)
    result = acquire(URL, scratch, deno, stage=events.append)
    assert events == ['SOURCE_CHECK', 'DOWNLOAD']
    assert result['size_bytes'] == 13 and len(result['sha256']) == 64


@pytest.mark.parametrize('protocol', ['m3u8_native', 'http_dash_segments', 'http', 'file'])
def test_external_or_fragment_downloader_never_selected(tmp_path, monkeypatch, protocol):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    deno = tmp_path / 'deno'
    deno.touch()
    monkeypatch.setattr(RestrictedYoutubeDL, 'extract_info', lambda *args, **kwargs:
        metadata() | {'protocol': protocol, 'vcodec': 'none', 'acodec': 'opus'})
    def forbidden(*args):
        raise AssertionError('unsafe downloader must not run')
    monkeypatch.setattr(RestrictedYoutubeDL, 'process_info', forbidden)
    with pytest.raises(ProcessFailure, match='FORMAT_UNSUPPORTED'):
        acquire(URL, scratch, deno)


def test_child_separates_downloader_text_from_status_protocol(monkeypatch, capsys):
    import json
    from meeting_minutes import youtube_child
    monkeypatch.setattr(youtube_child.sys, 'argv', ['child', URL, '/scratch', '/deno'])
    monkeypatch.setattr(youtube_child.resource, 'setrlimit', lambda *args: None)
    def noisy_acquire(*args, stage):
        stage('SOURCE_CHECK')
        print('[download] 100% fixture')
        stage('DOWNLOAD')
        return {'fixture': True}
    monkeypatch.setattr(youtube_child, 'acquire', noisy_acquire)
    youtube_child.main()
    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [
        {'stage': 'SOURCE_CHECK'}, {'stage': 'DOWNLOAD'}, {'result': {'fixture': True}}]
    assert '[download]' in captured.err

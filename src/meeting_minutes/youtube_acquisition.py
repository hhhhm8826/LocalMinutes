"""Restricted yt-dlp adapter, invoked only inside the acquisition subprocess."""
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeIE

from .files import file_hash
from .processes import ProcessFailure
from .youtube_policy import canonical_url, checked_metadata
from .youtube_transport import RestrictedRH, TransferBudget


class RestrictedYoutubeDL(YoutubeDL):
    def __init__(self, options, budget):
        self.transfer_budget = budget
        super().__init__(options, auto_init=False)
        self.add_info_extractor(YoutubeIE())

    def build_request_director(self, handlers, preferences=None):
        # Do not register urllib, requests, curl or fallback handlers.
        # Reuse yt-dlp's logger adapter and normalized handler options. YoutubeDL
        # itself does not implement the RequestDirector logger error interface.
        return super().build_request_director([
            lambda **options: RestrictedRH(budget=self.transfer_budget, **options)])


def acquire(url, directory: Path, deno: Path, *, stage=lambda _: None, max_bytes=2_000_000_000):
    url = canonical_url(url)
    if not directory.is_dir() or any(directory.iterdir()):
        raise ProcessFailure('YOUTUBE_SCRATCH_NOT_EMPTY')
    if not deno.is_absolute() or not deno.is_file():
        raise ProcessFailure('YOUTUBE_RUNTIME_REQUIRED')
    output = directory / 'audio.source'
    budget = TransferBudget(max_bytes=max_bytes + 32_000_000)

    def progress(status):
        budget.check()
        if status.get('downloaded_bytes', 0) > max_bytes:
            raise ProcessFailure('FILE_SIZE_LIMIT')
        if sum(path.stat().st_size for path in directory.iterdir() if path.is_file()) > max_bytes:
            raise ProcessFailure('FILE_SIZE_LIMIT')

    options = {'quiet': True, 'no_warnings': True, 'noplaylist': True, 'cachedir': False,
        'proxy': '', 'cookiefile': None, 'cookiesfrombrowser': None, 'usenetrc': False,
        'remote_components': [], 'js_runtimes': {'deno': {'path': str(deno)}},
        'format': 'bestaudio[protocol=https][vcodec=none]', 'outtmpl': str(output),
        'overwrites': False, 'continuedl': False, 'retries': 1, 'fragment_retries': 0,
        'extractor_retries': 1, 'file_access_retries': 0, 'socket_timeout': 15,
        'concurrent_fragment_downloads': 1, 'max_filesize': max_bytes,
        'writeinfojson': False, 'writethumbnail': False, 'writesubtitles': False,
        'writeautomaticsub': False, 'postprocessors': [], 'progress_hooks': [progress],
        'skip_unavailable_fragments': False, 'hls_prefer_native': True}
    with RestrictedYoutubeDL(options, budget) as downloader:
        stage('SOURCE_CHECK')
        info = downloader.extract_info(url, download=False)
        metadata = checked_metadata(info, url, max_bytes=max_bytes)
        # Only direct HTTPS audio is handed to the native HTTP downloader.
        # External ffmpeg/fragment downloaders must never bypass the handler.
        if (info.get('protocol') != 'https' or info.get('vcodec') != 'none'
                or info.get('acodec') in (None, 'none') or info.get('requested_formats')
                or info.get('fragments') or info.get('has_drm')):
            raise ProcessFailure('YOUTUBE_AUDIO_FORMAT_UNSUPPORTED')
        stage('DOWNLOAD')
        downloader.process_info(info)
    if not output.is_file() or not 0 < output.stat().st_size <= max_bytes:
        raise ProcessFailure('YOUTUBE_DOWNLOAD_INCOMPLETE')
    progress({})
    return {'path': str(output), 'size_bytes': output.stat().st_size, 'sha256': file_hash(output),
            'metadata': metadata}

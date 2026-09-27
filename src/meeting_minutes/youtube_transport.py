"""The sole yt-dlp request handler: bounded, no proxies/auth, vetted redirects."""
import io
import time
from urllib.parse import urljoin

from yt_dlp.networking.common import RequestHandler, Response
from yt_dlp.networking.exceptions import HTTPError, TransportError

from .processes import ProcessFailure
from .youtube_network import PublicHTTPSConnection, request_target


class TransferBudget:
    def __init__(self, *, max_bytes=2_032_000_000, max_requests=256, seconds=900):
        self.remaining_bytes = max_bytes
        self.remaining_requests = max_requests
        self.deadline = time.monotonic() + seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TransportError('YOUTUBE_TRANSFER_TIMEOUT')

    def request(self):
        self.check()
        if self.remaining_requests <= 0:
            raise TransportError('YOUTUBE_REQUEST_LIMIT')
        self.remaining_requests -= 1


class BudgetReader(io.RawIOBase):
    def __init__(self, response, connection, budget):
        self.response, self.connection, self.budget = response, connection, budget
        super().__init__()

    def readable(self):
        return True

    def read(self, size=-1):
        self.budget.check()
        # Metadata callers may request all bytes; stream consumers use bounded reads.
        if size is None or size < 0:
            chunks, total = [], 0
            while data := self.read(65536):
                total += len(data)
                if total > 32_000_000:
                    self.close()
                    raise TransportError('YOUTUBE_METADATA_SIZE_LIMIT')
                chunks.append(data)
            return b''.join(chunks)
        if size == 0:
            return b''
        data = self.response.read(min(size, self.budget.remaining_bytes + 1))
        self.budget.remaining_bytes -= len(data)
        if self.budget.remaining_bytes < 0:
            self.close()
            raise TransportError('YOUTUBE_TRANSFER_SIZE_LIMIT')
        self.budget.check()
        return data

    def close(self):
        if not self.closed:
            self.response.close()
            self.connection.close()
        super().close()


class RestrictedRH(RequestHandler):
    _SUPPORTED_URL_SCHEMES = ('https',)
    _SUPPORTED_PROXY_SCHEMES = ()

    def __init__(self, *, budget=None, **kwargs):
        super().__init__(**kwargs)
        self.budget = budget or TransferBudget()

    def _check_extensions(self, extensions):
        super()._check_extensions(extensions)
        for name in ('timeout', 'cookiejar', 'keep_header_casing'):
            extensions.pop(name, None)

    def _send(self, request):
        # yt-dlp represents explicit direct access as {'all': None}.
        if any(value not in (None, '') for proxies in (request.proxies, self.proxies)
               for value in proxies.values()):
            raise TransportError('YOUTUBE_PROXY_DENIED')
        url, method, data = request.url, request.method, request.data
        if method not in {'GET', 'HEAD', 'POST'} or (data is not None and (not isinstance(data, bytes) or len(data) > 1_000_000)):
            raise TransportError('YOUTUBE_REQUEST_DENIED')
        headers = {key: value for key, value in self._get_headers(request).items()
                   if key.lower() not in {'host', 'cookie', 'authorization', 'proxy-authorization', 'accept-encoding', 'connection'}}
        headers.update({'Accept-Encoding': 'identity', 'Connection': 'close'})
        for redirect in range(6):
            self.budget.request()
            connection = None
            try:
                request_target(url)
                connection = PublicHTTPSConnection(url, timeout=min(15, max(.1, self.budget.deadline - time.monotonic())))
                connection.request(method, connection.target, body=data, headers=headers)
                incoming = connection.getresponse()
                if incoming.status in {301, 302, 303, 307, 308}:
                    location = incoming.getheader('Location')
                    if not location or redirect == 5:
                        raise TransportError('YOUTUBE_REDIRECT_LIMIT')
                    target = urljoin(url, location)
                    request_target(target)
                    incoming.close()
                    connection.close()
                    if incoming.status == 303 or (incoming.status in {301, 302} and method == 'POST'):
                        method, data = 'GET', None
                        headers = {k: v for k, v in headers.items() if k.lower() not in {'content-type', 'content-length'}}
                    url = target
                    continue
                if incoming.getheader('Content-Encoding', 'identity') not in {'identity', ''}:
                    raise TransportError('YOUTUBE_ENCODING_DENIED')
                length = incoming.getheader('Content-Length')
                if length and (not length.isdigit() or int(length) > self.budget.remaining_bytes):
                    raise TransportError('YOUTUBE_TRANSFER_SIZE_LIMIT')
                response = Response(BudgetReader(incoming, connection, self.budget), url,
                                    dict(incoming.getheaders()), incoming.status, incoming.reason)
                if incoming.status >= 400:
                    raise HTTPError(response)
                return response
            except HTTPError:
                raise
            except (ProcessFailure, OSError, ValueError, TransportError) as exc:
                if connection:
                    connection.close()
                if isinstance(exc, TransportError):
                    raise
                raise TransportError(str(exc)) from exc
        raise TransportError('YOUTUBE_REDIRECT_LIMIT')

"""HTTPS connections pinned to vetted public addresses for acquisition only."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

from .processes import ProcessFailure

EXACT_HOSTS = {'www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtubei.googleapis.com'}


def request_target(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        allowed = host in EXACT_HOSTS or (host and host.endswith('.googlevideo.com'))
        if (parsed.scheme != 'https' or not allowed or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or '\\' in url or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError
    except ValueError as exc:
        raise ProcessFailure('YOUTUBE_NETWORK_TARGET_DENIED') from exc
    return host, (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')


def public_addresses(host):
    """Reject mixed public/private answers, including mapped and transition IPv6."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            raise ValueError
        for value in addresses:
            address = ipaddress.ip_address(value)
            if (not address.is_global or address.is_multicast or address.is_unspecified
                    or (address.version == 6 and (address.ipv4_mapped or address.sixtofour or address.teredo))):
                raise ValueError
    except (ValueError, OSError) as exc:
        raise ProcessFailure('YOUTUBE_NETWORK_ADDRESS_DENIED') from exc
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    """Resolve once, connect to that numeric IP, validate TLS for original host.

    The acquisition handler must construct a fresh connection for every request,
    including redirects. No environment proxies, CONNECT tunnel or second DNS
    lookup is used. This does not itself provide filesystem/process isolation.
    """
    def __init__(self, url, *, timeout=15):
        host, self.target = request_target(url)
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())

    def connect(self):
        if self._tunnel_host:
            raise ProcessFailure('YOUTUBE_NETWORK_TARGET_DENIED')
        addresses = public_addresses(self.host)
        last_error = None
        for address in addresses[:4]:
            raw = None
            try:
                family = socket.AF_INET6 if ':' in address else socket.AF_INET
                raw = socket.socket(family, socket.SOCK_STREAM)
                raw.settimeout(self.timeout)
                raw.connect((address, 443))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    raw.close()
        raise last_error or ProcessFailure('YOUTUBE_NETWORK_UNAVAILABLE')

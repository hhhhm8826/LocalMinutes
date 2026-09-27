import socket
from unittest.mock import Mock

import pytest

from meeting_minutes.processes import ProcessFailure
from meeting_minutes.youtube_network import PublicHTTPSConnection, public_addresses, request_target


@pytest.mark.parametrize('url', ['https://evilgooglevideo.com/x', 'https://www.youtube.com.evil/x',
    'https://127.0.0.1/x', 'https://169.254.169.254/latest', 'file:///etc/passwd',
    'http://www.youtube.com/x', 'https://user@www.youtube.com/x', 'https://www.youtube.com:8443/x'])
def test_extractor_and_redirect_targets_restricted(url):
    with pytest.raises(ProcessFailure, match='TARGET_DENIED'):
        request_target(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.2',
    '100.64.0.1', '::1', 'fe80::1', 'fc00::1', '::ffff:127.0.0.1', '2002:7f00:1::', '224.0.0.1'])
def test_mixed_dns_answers_fail_closed(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (2, 1, 6, '', ('142.250.1.1', 443)), (2, 1, 6, '', (address, 443))])
    with pytest.raises(ProcessFailure, match='ADDRESS_DENIED'):
        public_addresses('www.youtube.com')


def test_connect_uses_validated_numeric_address_and_original_tls_name(monkeypatch):
    resolve = Mock(return_value=[(2, 1, 6, '', ('142.250.1.1', 443))])
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    raw = Mock()
    monkeypatch.setattr(socket, 'socket', Mock(return_value=raw))
    connection = PublicHTTPSConnection('https://www.youtube.com/watch?v=AbCde_123-4')
    tls = Mock()
    connection._context = tls
    connection.connect()
    raw.connect.assert_called_once_with(('142.250.1.1', 443))
    assert resolve.call_count == 1
    tls.wrap_socket.assert_called_once_with(raw, server_hostname='www.youtube.com')
    assert connection.target == '/watch?v=AbCde_123-4'

import io
from unittest.mock import Mock

import pytest
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import TransportError

from meeting_minutes.youtube_transport import BudgetReader, RestrictedRH, TransferBudget


class Incoming(io.BytesIO):
    def __init__(self, data=b'abc', status=200, headers=None):
        super().__init__(data)
        self.status, self.reason, self.headers = status, 'fixture', headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())


def handler(monkeypatch, incoming):
    connections = []
    def factory(url, **kwargs):
        connection = Mock(target='/fixture')
        connection.getresponse.return_value = incoming.pop(0)
        connections.append((url, connection))
        return connection
    monkeypatch.setattr('meeting_minutes.youtube_transport.PublicHTTPSConnection', factory)
    return RestrictedRH(logger=Mock(), budget=TransferBudget(max_bytes=100)), connections


def test_every_redirect_revalidated_before_connect(monkeypatch):
    transport, connections = handler(monkeypatch, [Incoming(status=302, headers={'Location': 'http://169.254.169.254/latest'})])
    with pytest.raises(TransportError, match='TARGET_DENIED'):
        transport.send(Request('https://www.youtube.com/watch?v=AbCde_123-4'))
    assert len(connections) == 1
    connections[0][1].close.assert_called_once()


def test_redirect_public_cdn_and_strip_inherited_credentials(monkeypatch):
    transport, connections = handler(monkeypatch, [Incoming(status=302, headers={'Location': 'https://r1.googlevideo.com/audio'}), Incoming()])
    response = transport.send(Request('https://www.youtube.com/watch?v=AbCde_123-4',
        headers={'Cookie': 'secret', 'Authorization': 'secret', 'Host': 'evil', 'Accept-Encoding': 'gzip'}))
    assert response.read() == b'abc'
    assert len(connections) == 2
    for _, connection in connections:
        headers = connection.request.call_args.kwargs['headers']
        assert not {'cookie', 'authorization', 'host'} & {key.lower() for key in headers}
        assert headers['Accept-Encoding'] == 'identity'
    response.close()
    connections[-1][1].close.assert_called_once()


def test_cumulative_unknown_length_budget_counts_across_responses():
    budget = TransferBudget(max_bytes=5)
    first = BudgetReader(Incoming(b'abc'), Mock(), budget)
    assert first.read(3) == b'abc'
    second = BudgetReader(Incoming(b'def'), Mock(), budget)
    with pytest.raises(TransportError, match='SIZE_LIMIT'):
        second.read(10)
    assert second.closed


@pytest.mark.parametrize('headers,code', [({'Content-Length': '101'}, 'SIZE_LIMIT'),
    ({'Content-Encoding': 'gzip'}, 'ENCODING_DENIED')])
def test_headers_cannot_bypass_budget(monkeypatch, headers, code):
    transport, _ = handler(monkeypatch, [Incoming(headers=headers)])
    with pytest.raises(TransportError, match=code):
        transport.send(Request('https://www.youtube.com/'))


def test_request_limit_and_deadline():
    budget = TransferBudget(max_requests=1)
    budget.request()
    with pytest.raises(TransportError, match='REQUEST_LIMIT'):
        budget.request()
    budget.deadline = 0
    with pytest.raises(TransportError, match='TIMEOUT'):
        budget.check()


def test_real_director_direct_access_sentinel(monkeypatch):
    from meeting_minutes.youtube_acquisition import RestrictedYoutubeDL
    _, connections = handler(monkeypatch, [Incoming()])
    with RestrictedYoutubeDL({'quiet': True, 'proxy': '', 'cachedir': False}, TransferBudget()) as downloader:
        response = downloader.urlopen(Request('https://www.youtube.com/'))
        assert response.read() == b'abc'
        response.close()
    assert len(connections) == 1


@pytest.mark.parametrize('location', ['request', 'handler'])
def test_actual_proxy_denied_before_connect(monkeypatch, location):
    transport, connections = handler(monkeypatch, [])
    request = Request('https://www.youtube.com/')
    if location == 'request':
        request.proxies = {'https': 'https://proxy.example:443'}
    else:
        transport.proxies = {'all': 'https://proxy.example:443'}
    with pytest.raises(TransportError):
        transport.send(request)
    assert connections == []

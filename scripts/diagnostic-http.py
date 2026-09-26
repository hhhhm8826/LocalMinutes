"""루프백 연결 확인용 임시 서버. 제품 API나 파일 서버가 아니다."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import time


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>LocalMinutes Preflight</title><h1>WSL loopback diagnostic OK</h1>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(json.dumps({'client': self.client_address[0], 'status': args[1] if len(args)>1 else ''}), flush=True)


server = HTTPServer(('127.0.0.1', 8766), Handler)
server.timeout = 1
deadline = time.monotonic() + 180
print('LISTEN 127.0.0.1:8766; expires after 180 seconds', flush=True)
while time.monotonic() < deadline:
    server.handle_request()
server.server_close()

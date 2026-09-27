"""소유자 키는 로컬 파일에만, 세션 토큰은 해시로 저장한다."""
import hashlib
import os
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import text


COOKIE = "minutes_session"
LOCAL_COOKIE = 'minutes_local_session'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def prepare_owner_key(path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("소유자 키는 일반 파일이어야 합니다")
        path.chmod(0o600)
        return
    with os.fdopen(descriptor, "w") as stream:
        stream.write(secrets.token_urlsafe(32))
        stream.flush()
        os.fsync(stream.fileno())


def session_row(request, table, cookie):
    token = request.cookies.get(cookie, '')
    if not token or len(token) > 200:
        return None
    with request.app.state.engine.connect() as connection:
        row = connection.execute(text(f"SELECT csrf_token, expires_at FROM {table} WHERE token_hash=:hash"),
                                 {"hash": digest(token)}).mappings().first()
    return dict(row) if row and row['expires_at'] > time.time() else None


def has_owner(request):
    return session_row(request, 'owner_sessions', COOKIE) is not None


def ensure_local_session(request, response):
    existing = session_row(request, 'local_sessions', LOCAL_COOKIE)
    if existing:
        return existing
    token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
    with request.app.state.engine.begin() as connection:
        connection.execute(text('DELETE FROM local_sessions WHERE expires_at<=:now'), {'now': now})
        connection.execute(text('INSERT INTO local_sessions VALUES (:hash,:csrf,:expiry)'),
                           {'hash': digest(token), 'csrf': csrf, 'expiry': now + 43200})
    response.set_cookie(LOCAL_COOKIE, token, httponly=True, samesite='strict', max_age=43200, path='/')
    return {'csrf_token': csrf, 'expires_at': now + 43200}


def local_session(request: Request):
    row = session_row(request, 'local_sessions', LOCAL_COOKIE)
    if not row:
        raise HTTPException(401, '로컬 세션을 다시 열어주세요')
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf_token"]):
            raise HTTPException(403, "요청 검증에 실패했습니다")
    return dict(row)


def owner_session(request: Request):
    if not has_owner(request):
        raise HTTPException(401, '로컬 소유자 인증이 필요합니다')
    return local_session(request)

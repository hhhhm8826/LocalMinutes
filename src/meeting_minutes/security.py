"""소유자 키는 로컬 파일에만, 세션 토큰은 해시로 저장한다."""
import hashlib
import os
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import text


COOKIE = "minutes_session"


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


def owner_session(request: Request):
    token = request.cookies.get(COOKIE, "")
    if len(token) > 200:
        raise HTTPException(401, "로컬 소유자 인증이 필요합니다")
    with request.app.state.engine.connect() as connection:
        row = connection.execute(text("SELECT csrf_token, expires_at FROM owner_sessions WHERE token_hash=:hash"),
                                 {"hash": digest(token)}).mappings().first()
    if not row or row["expires_at"] <= time.time():
        raise HTTPException(401, "로컬 소유자 인증이 필요합니다")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf_token"]):
            raise HTTPException(403, "요청 검증에 실패했습니다")
    return dict(row)

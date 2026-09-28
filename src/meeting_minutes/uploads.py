"""오디오·영상 원본을 스트리밍 저장한 다음에만 FIFO에 등록한다."""
import hashlib
import json
import os
from pathlib import Path
import re

from fastapi import HTTPException, Request

from .files import safe_file, sync_directory
from .repository import identifier
from .deletion import meeting_file_lock


EXTENSIONS = {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.mp4', '.mov', '.mkv', '.webm'}


async def receive_upload(request: Request, meeting_id: str, filename: str, key: str):
    try:
        with meeting_file_lock(request.app.state.settings, meeting_id):
            return await receive_locked_upload(request, meeting_id, filename, key)
    except BlockingIOError as exc:
        raise HTTPException(409, '회의 자료를 정리 중입니다') from exc


async def receive_locked_upload(request: Request, meeting_id: str, filename: str, key: str):
    settings, repository = request.app.state.settings, request.app.state.repository
    repository.meeting(meeting_id)
    if (not filename or len(filename) > 240 or any(ord(c) < 32 for c in filename)
            or '/' in filename or '\\' in filename or Path(filename).suffix.lower() not in EXTENSIONS):
        raise HTTPException(422, '지원하는 미디어 파일 이름이 필요합니다')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', key):
        raise HTTPException(422, '유효한 Idempotency-Key가 필요합니다')
    length = request.headers.get('content-length')
    if length is not None:
        try:
            declared = int(length)
        except ValueError as exc:
            raise HTTPException(400, '잘못된 업로드 길이입니다') from exc
        if declared <= 0 or declared > settings.max_upload_bytes:
            raise HTTPException(413, '파일 크기는 1바이트 이상 2GB 이하여야 합니다')
    root = settings.data_dir / 'media'
    name = meeting_id + '-' + identifier() + Path(filename).suffix.lower()
    partial = safe_file(root, name + '.part')
    final = safe_file(root, name)
    committed = False
    size, digest = 0, hashlib.sha256()
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            async for chunk in request.stream():
                repository.meeting(meeting_id)
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, '파일 크기는 2GB 이하여야 합니다')
                stream.write(chunk)
                digest.update(chunk)
            if size == 0 or (length is not None and size != int(length)):
                raise HTTPException(400, '업로드가 끝까지 전달되지 않았습니다')
            stream.flush()
            os.fsync(stream.fileno())
        partial.replace(final)
        sync_directory(root)
        request_hash = hashlib.sha256(json.dumps([meeting_id, filename, digest.hexdigest(), size]).encode()).hexdigest()
        result, created = repository.register_media(meeting_id, filename, name, size, digest.hexdigest(), key, request_hash, settings=settings)
        committed = created
        return result
    except OSError as exc:
        raise HTTPException(507 if exc.errno == 28 else 500, '파일을 저장할 수 없습니다') from exc
    finally:
        partial.unlink(missing_ok=True)
        if not committed:
            final.unlink(missing_ok=True)

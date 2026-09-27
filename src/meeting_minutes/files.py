"""서버가 생성한 파일명만 열며 심볼릭 링크를 따르지 않는다."""
import hashlib
import os
from pathlib import Path
import re


def safe_file(root: Path, name: str):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,180}', name) or name in {'.', '..'}:
        raise ValueError('invalid storage key')
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('unsafe storage path')
    return path


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_hash(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

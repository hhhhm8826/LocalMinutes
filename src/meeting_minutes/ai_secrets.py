"""단일 Gemini 키의 원자적 교체. 원문은 생성 어댑터에만 전달합니다."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import os
import secrets
import stat
import time

from pydantic import SecretStr


class SecretStoreError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Credential:
    key: SecretStr = field(repr=False)
    revision: str
    updated_at: float

    def status(self):
        return {'registered': True, 'credential_revision': self.revision, 'updated_at': self.updated_at}


def validate_key(value):
    if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise SecretStoreError('AI_KEY_INPUT_INVALID')
    return value


def _check_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise SecretStoreError('AI_SECRET_PATH_UNSAFE')


class GeminiSecretStore:
    def __init__(self, settings):
        self.config = settings.config_dir
        # Secret storage must never be served or collected as a media artifact.
        public = [settings.data_dir / 'media', settings.data_dir / 'artifacts']
        if settings.web_dir:
            public.append(settings.web_dir)
        if any(self.config.resolve().is_relative_to(p.resolve()) for p in public):
            raise SecretStoreError('AI_SECRET_PATH_UNSAFE')

    @contextmanager
    def locked(self):
        descriptors = []
        lock = None
        try:
            # Open every path component without following links, not only the leaf.
            if not self.config.is_absolute() or '..' in self.config.parts:
                raise SecretStoreError('AI_SECRET_PATH_UNSAFE')
            current = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(current)
            for part in self.config.parts[1:]:
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(current)
            info = os.fstat(current)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SecretStoreError('AI_SECRET_PATH_UNSAFE')
            try:
                os.mkdir('.apikey', mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            parent = current
            directory = os.open('.apikey', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(directory)
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SecretStoreError('AI_SECRET_PATH_UNSAFE')
            lock = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            _check_file(lock)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SecretStoreError('AI_SECRET_BUSY') from None
                    time.sleep(.02)
            visible = os.stat('.apikey', dir_fd=parent, follow_symlinks=False)
            if (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
                raise SecretStoreError('AI_SECRET_PATH_UNSAFE')
            # Recover interrupted writes only while holding the same process lock.
            for name in os.listdir(directory):
                if name.startswith('.gemini-') and name.endswith('.tmp'):
                    os.unlink(name, dir_fd=directory)
            yield directory
        except OSError:
            raise SecretStoreError('AI_SECRET_IO_FAILED') from None
        finally:
            if lock is not None:
                os.close(lock)
            for fd in reversed(descriptors):
                os.close(fd)

    def _read(self, directory):
        try:
            fd = os.open('gemini.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            _check_file(fd)
            if os.fstat(fd).st_size > 8192:
                raise SecretStoreError('AI_SECRET_FILE_INVALID')
            raw = os.read(fd, 8193)
        finally:
            os.close(fd)
        try:
            value = json.loads(raw)
            key = validate_key(value['key'])
            revision, updated = value['credential_revision'], value['updated_at']
            if not isinstance(revision, str) or len(revision) != 32 or any(c not in '0123456789abcdef' for c in revision):
                raise ValueError()
            if type(updated) not in (int, float) or not 0 < updated < 1e12:
                raise ValueError()
            return Credential(SecretStr(key), revision, updated)
        except (KeyError, TypeError, ValueError, SecretStoreError):
            raise SecretStoreError('AI_SECRET_FILE_INVALID') from None

    def read(self):
        with self.locked() as directory:
            return self._read(directory)

    def status(self):
        current = self.read()
        return current.status() if current else {'registered': False, 'credential_revision': None, 'updated_at': None}

    def replace(self, key, expected_revision):
        key = validate_key(key)
        with self.locked() as directory:
            previous = self._read(directory)
            if (previous.revision if previous else None) != expected_revision:
                raise SecretStoreError('AI_CREDENTIAL_REVISION_CONFLICT')
            current = Credential(SecretStr(key), secrets.token_hex(16), time.time())
            name = '.gemini-' + secrets.token_hex(16) + '.tmp'
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'key': key, 'credential_revision': current.revision, 'updated_at': current.updated_at}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, 'gemini.json', src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
            return current.status()

    def delete(self, expected_revision):
        with self.locked() as directory:
            current = self._read(directory)
            if (current.revision if current else None) != expected_revision:
                raise SecretStoreError('AI_CREDENTIAL_REVISION_CONFLICT')
            if current:
                os.unlink('gemini.json', dir_fd=directory)
                os.fsync(directory)
            return {'registered': False, 'credential_revision': None, 'updated_at': None}

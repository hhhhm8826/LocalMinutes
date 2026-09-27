"""사용자 영역 운영 도구. 백업에서 인증·로그·임시 결과를 제외한다."""
import argparse
import ctypes
from contextlib import closing, contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import time

import psutil

from .settings import Settings
from .process_identity import matches


@contextmanager
def stopped_locks(settings):
    handles = []
    try:
        for name in ('app.lock', 'worker.lock'):
            handle = (settings.data_dir / name).open('a+')
            handles.append(handle)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise RuntimeError('먼저 앱과 작업 관리자를 정지하세요.') from None
    finally:
        for handle in handles:
            handle.close()


def stop(settings):
    record = settings.data_dir / 'app-process.json'
    if not record.exists():
        return {'status': 'not_running'}
    value = json.loads(record.read_text())
    try:
        process = psutil.Process(value['pid'])
        command = process.cmdline()
        index = command.index('--data-dir')
        same = matches(process.pid, value['identity']) if 'identity' in value else abs(process.create_time() - value['created_at']) < .01
        if (not same or 'meeting_minutes.server' not in command
                or Path(command[index + 1]).resolve() != settings.data_dir or value['data_dir'] != str(settings.data_dir)):
            raise RuntimeError('프로세스 소유권을 확인할 수 없어 종료하지 않았습니다.')
        process.terminate()
        deadline = time.monotonic() + 20
        while process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
            if time.monotonic() >= deadline:
                raise psutil.TimeoutExpired(20, pid=process.pid)
            time.sleep(.05)
        # A non-parent cannot reap a zombie. Its exit has already released
        # resources; both locks also prove the worker has finished cleanup.
        with stopped_locks(settings):
            pass
    except psutil.NoSuchProcess:
        return {'status': 'not_running'}
    except (ValueError, IndexError, KeyError):
        raise RuntimeError('프로세스 식별 정보가 일치하지 않습니다.') from None
    except psutil.TimeoutExpired:
        raise RuntimeError('종료 확인 시간 초과: 자식 정리 상태를 확인하세요. 강제 종료하지 않았습니다.') from None
    return {'status': 'stopped'}


def backup(settings, destination):
    destination = destination.expanduser().resolve()
    if destination.exists() or destination.is_relative_to(settings.data_dir):
        raise RuntimeError('데이터 폴더 밖의 새 백업 파일 경로가 필요합니다.')
    if not settings.database_path.is_file():
        raise RuntimeError('백업할 데이터베이스가 없습니다.')
    with stopped_locks(settings), tempfile.TemporaryDirectory(prefix='minutes-backup-') as folder:
        database = Path(folder) / 'minutes.sqlite3'
        with closing(sqlite3.connect(settings.database_path)) as source, closing(sqlite3.connect(database)) as target:
            source.backup(target)
            target.execute('DELETE FROM owner_sessions')
            if target.execute("SELECT 1 FROM sqlite_master WHERE name='local_sessions'").fetchone():
                target.execute('DELETE FROM local_sessions')
            target.commit()
            target.execute('VACUUM')
            target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            target.execute('PRAGMA journal_mode=DELETE')
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as output, tarfile.open(fileobj=output, mode='w:gz') as archive:
                files = {}
                def add(path, name):
                    with path.open('rb') as stream:
                        files[name] = {'size': path.stat().st_size,
                                       'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
                    archive.add(path, arcname=name)
                add(database, 'minutes.sqlite3')
                for name in ('media', 'artifacts'):
                    root = settings.data_dir / name
                    for path in root.iterdir():
                        if path.is_symlink():
                            raise RuntimeError('심볼릭 링크 자료는 백업하지 않습니다.')
                        if path.is_file() and not path.name.endswith('.part'):
                            add(path, name + '/' + path.name)
                payload = json.dumps({'schema_version': 1, 'files': files}, sort_keys=True).encode()
                if len(payload) > 8_000_000:
                    raise RuntimeError('백업 manifest 크기 제한을 초과했습니다.')
                member = tarfile.TarInfo('manifest.json')
                member.size, member.mode = len(payload), 0o600
                archive.addfile(member, io.BytesIO(payload))
            destination.chmod(0o600)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return {'status': 'backed_up', 'path': str(destination)}


def restore(source, destination):
    source, destination = source.expanduser().resolve(), destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('복원 대상은 존재하지 않는 새 데이터 디렉터리여야 합니다.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.minutes-restore-', dir=destination.parent) as folder:
        root = Path(folder) / 'data'
        root.mkdir(mode=0o700)
        with tarfile.open(source, mode='r:gz') as archive:
            members = archive.getmembers()
            names = set()
            for member in members:
                path = Path(member.name)
                if (not member.isfile() or member.name in names or path.is_absolute() or '..' in path.parts
                        or not (member.name in {'minutes.sqlite3', 'manifest.json'} or len(path.parts) == 2 and path.parts[0] in {'media', 'artifacts'})):
                    raise RuntimeError('허용되지 않는 백업 항목입니다.')
                names.add(member.name)
            if 'minutes.sqlite3' not in names:
                raise RuntimeError('백업 데이터베이스가 없습니다.')
            if 'manifest.json' not in names or archive.getmember('manifest.json').size > 8_000_000:
                raise RuntimeError('백업 manifest가 없거나 너무 큽니다.')
            with archive.extractfile('manifest.json') as incoming:
                manifest = json.load(incoming)
            if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
                    or not isinstance(manifest.get('files'), dict)
                    or set(manifest['files']) != names - {'manifest.json'}):
                raise RuntimeError('백업 manifest 파일 목록이 일치하지 않습니다.')
            if sum(member.size for member in members) > shutil.disk_usage(destination.parent).free:
                raise RuntimeError('복원 공간이 부족합니다.')
            for member in members:
                target = root / member.name
                target.parent.mkdir(exist_ok=True, mode=0o700)
                with archive.extractfile(member) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(0o600)
                if member.name != 'manifest.json':
                    with target.open('rb') as stream:
                        actual = {'size': target.stat().st_size,
                                  'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
                    if manifest['files'][member.name] != actual:
                        raise RuntimeError('백업 파일 크기 또는 해시가 일치하지 않습니다.')
            (root / 'manifest.json').unlink()
        with closing(sqlite3.connect(root / 'minutes.sqlite3')) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('백업 데이터베이스 무결성 검사에 실패했습니다.')
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('백업 데이터베이스 참조가 손상됐습니다.')
            connection.execute('DELETE FROM owner_sessions')
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='local_sessions'").fetchone():
                connection.execute('DELETE FROM local_sessions')
            connection.commit()
            connection.execute('VACUUM')
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            connection.execute('PRAGMA journal_mode=DELETE')
        # 새 대상만 허용하므로 기존 데이터나 인증을 덮어쓰지 않는다.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(root), -100, os.fsencode(destination), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
    return {'status': 'restored', 'data_dir': str(destination), 'login_required': True}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('stop')
    commands.add_parser('backup').add_argument('destination', type=Path)
    command = commands.add_parser('restore')
    command.add_argument('source', type=Path)
    command.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        settings = Settings()
        result = stop(settings) if args.command == 'stop' else backup(settings, args.destination) if args.command == 'backup' else restore(args.source, args.destination)
        print(json.dumps(result, ensure_ascii=False))
    except (RuntimeError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()

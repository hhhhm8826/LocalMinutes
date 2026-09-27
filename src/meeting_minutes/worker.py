"""단일 관리자와 시도별 프로세스. 재시작은 명시적 복구를 기다린다."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

import psutil
from sqlalchemy import text

from .files import safe_file
from .processes import group_members, stop_group
from .repository import Repository
from .settings import Settings
from .storage import make_engine
from .deletion import reap_deletions
from .retention import reap_retention
from .process_identity import boot_id, identity, matches
from .temporary import cleanup_attempt_temporary


def worker_env(settings):
    env = os.environ.copy()
    for field in ('data_dir', 'config_dir', 'cache_dir', 'max_duration_seconds', 'threads', 'codex_cli',
                  'codex_home', 'codex_user_home', 'codex_model', 'codex_timeout_seconds', 'codex_input_bytes', 'codex_max_calls'):
        env['MINUTES_' + field.upper()] = str(getattr(settings, field))
    return env


class Worker:
    def __init__(self, settings, engine, command=None):
        self.settings, self.repository = settings, Repository(engine)
        # 주입은 테스트 코드에서만 사용하며 제품 설정에 fake 경로를 노출하지 않는다.
        self.command = command or [sys.executable, '-m', 'meeting_minutes.job_runner']
        self.stopping = False
        self.lock = None

    def acquire(self):
        self.lock = (self.settings.data_dir / 'worker.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            return False
        return True

    def close(self):
        if self.lock:
            self.lock.close()
            self.lock = None

    def cleanup_attempt(self, job):
        # 명시된 시도의 임시 파일만 지운다. 완료 산출물은 재시도에서 재사용한다.
        cleanup_attempt_temporary(job['id'], job['attempt_id'])
        for suffix in ('.wav.part', '.result.part', '-transcribe.json.part', '-align.json.part', '-diarize.json.part', '-summarize.json.part'):
            partial = safe_file(self.settings.data_dir / 'artifacts', f'{job["id"]}-{job["attempt_id"]}{suffix}')
            partial.unlink(missing_ok=True)
        root = self.settings.data_dir / 'artifacts'
        for partial in root.glob(f'{job["id"]}-{job["attempt_id"]}-*.json.part'):
            safe_file(root, partial.name).unlink(missing_ok=True)

    def recover(self):
        with self.repository.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(text(
                "SELECT * FROM jobs WHERE state IN ('RUNNING','CANCEL_REQUESTED')")).mappings()]
        for row in rows:
            pid = row['pid']
            stored = json.loads(row['process_identity_json']) if row.get('process_identity_json') else None
            if stored and stored.get('boot_id') != boot_id():
                # 이전 부팅의 프로세스는 존재하지 않는다. 재사용된 현재 PID에 신호를 보내지 않는다.
                pid = None
            if pid:
                try:
                    process = psutil.Process(pid)
                    same_process = matches(pid, stored) if stored else abs(process.create_time() - row['process_created_at']) < .01
                    same = (same_process
                            and os.getpgid(pid) == pid and row['attempt_id'] in process.cmdline()
                            and row['id'] in process.cmdline())
                    if same:
                        if not stop_group(pid):
                            raise RuntimeError('PROCESS_STOP_UNCONFIRMED')
                    elif group_members(pid):
                        raise RuntimeError('PROCESS_OWNERSHIP_UNCERTAIN')
                except (psutil.NoSuchProcess, ProcessLookupError):
                    if group_members(pid):
                        raise RuntimeError('ORPHAN_GROUP_OWNERSHIP_UNCERTAIN')
            self.cleanup_attempt(row)
            self.repository.finish(row['id'], row['attempt_id'],
                                   'CANCELLED' if row['cancel_requested'] else 'INTERRUPTED', 'MANAGER_RESTARTED')

    def execute(self, job):
        result_path = safe_file(self.settings.data_dir / 'artifacts', f'{job["id"]}-{job["attempt_id"]}.result.part')
        process = None
        try:
            descriptor = os.open(result_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                process = subprocess.Popen([*self.command, '--job', job['id'], '--attempt', job['attempt_id']],
                    env=worker_env(self.settings), stdin=subprocess.PIPE, stdout=output,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                created_at = psutil.Process(process.pid).create_time()
                if not self.repository.set_process(job['id'], job['attempt_id'], process.pid, created_at, identity(process.pid)):
                    stop_group(process.pid)
                    process.wait()
                    self.repository.finish(job['id'], job['attempt_id'], 'CANCELLED')
                    return
                process.stdin.write(b'START\n')
                process.stdin.flush()
                process.stdin.close()
                stop_state = None
                while process.poll() is None:
                    current = self.repository.job(job['id'])
                    if current['cancel_requested']:
                        stop_state = ('CANCELLED', None)
                    elif self.stopping:
                        stop_state = ('INTERRUPTED', 'MANAGER_STOPPED')
                    elif result_path.stat().st_size > 65536:
                        stop_state = ('FAILED', 'WORKER_OUTPUT_LIMIT')
                    if stop_state:
                        if not stop_group(process.pid):
                            raise RuntimeError('PROCESS_STOP_UNCONFIRMED')
                        break
                    time.sleep(.1)
                process.wait()
                if group_members(process.pid) and not stop_group(process.pid):
                    raise RuntimeError('DESCENDANTS_STILL_RUNNING')
            current = self.repository.job(job['id'])
            if current['cancel_requested']:
                stop_state = ('CANCELLED', None)
            if stop_state:
                self.cleanup_attempt(job)
                self.repository.finish(job['id'], job['attempt_id'], *stop_state)
                return
            state, reason = 'FAILED', 'WORKER_EXITED'
            if process.returncode == 0 and result_path.stat().st_size <= 65536:
                try:
                    result = json.loads(result_path.read_text())
                    if result['state'] in {'COMPLETED', 'COMPLETED_TRANSCRIPT_ONLY', 'BLOCKED', 'FAILED'}:
                        state, reason = result['state'], result.get('reason')
                except (ValueError, KeyError):
                    pass
            finished = self.repository.finish(job['id'], job['attempt_id'], state, reason)
            if not finished:
                # 종료 확인과 DB 확정 사이에 도착한 취소도 유실하지 않는다.
                current = self.repository.job(job['id'])
                if current['attempt_id'] == job['attempt_id'] and current['state'] == 'CANCEL_REQUESTED':
                    self.cleanup_attempt(job)
                    self.repository.finish(job['id'], job['attempt_id'], 'CANCELLED')
        finally:
            if process is not None and process.poll() is None:
                stop_group(process.pid)
                process.wait()
            self.cleanup_attempt(job)
            result_path.unlink(missing_ok=True)

    def run(self):
        if not self.acquire():
            return False
        try:
            self.recover()
            last_retention = 0
            while not self.stopping:
                reap_deletions(self.repository, self.settings)
                if time.monotonic() - last_retention >= 60:
                    reap_retention(self.repository, self.settings)
                    last_retention = time.monotonic()
                job = self.repository.claim()
                if job:
                    self.execute(job)
                else:
                    time.sleep(.2)
            return True
        finally:
            self.close()


def main():
    os.umask(0o077)
    settings = Settings()
    settings.prepare()
    engine = make_engine(settings.database_path)
    worker = Worker(settings, engine)
    def stop(*_):
        worker.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        worker.run()
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()

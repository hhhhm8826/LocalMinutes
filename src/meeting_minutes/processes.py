"""출력·시간·파일 크기 제한과 프로세스 그룹 종료."""
import os
import resource
import selectors
import signal
import subprocess
import time

import psutil


class ProcessFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def bounded_run(argv, *, timeout, output_limit=262144, file_limit=300_000_000):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, preexec_fn=limits)
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in buffers:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ProcessFailure('PROCESS_TIMEOUT')
            for key, _ in selector.select(.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[key.fileobj].extend(chunk)
                if len(buffers[key.fileobj]) > output_limit:
                    raise ProcessFailure('PROCESS_OUTPUT_LIMIT')
        process.wait(timeout=max(.1, deadline - time.monotonic()))
        if process.returncode:
            raise ProcessFailure('MEDIA_DECODE_FAILED')
        return bytes(buffers[process.stdout])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()


def group_members(pgid):
    members = []
    for process in psutil.process_iter(['pid', 'status']):
        try:
            if os.getpgid(process.pid) == pgid and process.status() != psutil.STATUS_ZOMBIE:
                members.append(process)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return members


def stop_group(pgid, grace=5):
    if pgid <= 1 or pgid == os.getpgrp():
        raise RuntimeError('refuse unrelated process group')
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not group_members(pgid):
            return True
        time.sleep(.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not group_members(pgid):
            return True
        time.sleep(.05)
    return False

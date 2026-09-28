"""Bounded stdin/stdout process execution shared by subscription CLI adapters."""
import os
import resource
import selectors
import subprocess
import time

import psutil
from .ai_common import AIFailure


def kill_tree(process):
    # Keep the CLI in the enclosing job group so normal job cancellation also reaches it.
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        if process.poll() is None:
            process.kill()
        psutil.wait_procs(children, timeout=3)
    except psutil.NoSuchProcess:
        pass
    process.wait()


def bounded_cli(argv, *, cwd, env, input_bytes=b'', timeout=180, output_limit=4_000_000, result_path=None, error_prefix='CODEX'):
    def limits():
        # CLI SQLite/WAL files share this limit; result/stdout have smaller independent caps.
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, preexec_fn=limits)
    selector = selectors.DefaultSelector()
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in buffers:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    offset = 0
    if input_bytes:
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
    else:
        process.stdin.close()
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                raise AIFailure(error_prefix + '_TIMEOUT')
            if result_path is not None and result_path.exists() and result_path.stat().st_size > 1_000_000:
                raise AIFailure(error_prefix + '_OUTPUT_LIMIT')
            for key, _ in selector.select(.1):
                if key.fileobj is process.stdin:
                    try:
                        offset += os.write(process.stdin.fileno(), input_bytes[offset:offset + 16384])
                    except BrokenPipeError:
                        offset = len(input_bytes)
                    if offset == len(input_bytes):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.fileobj].extend(chunk)
                    if sum(map(len, buffers.values())) > output_limit:
                        raise AIFailure(error_prefix + '_OUTPUT_LIMIT')
        process.wait()
        return process.returncode, bytes(buffers[process.stdout]), bytes(buffers[process.stderr])
    finally:
        if process.poll() is None:
            kill_tree(process)
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

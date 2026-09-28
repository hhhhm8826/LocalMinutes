"""One external call per provider across workers, with cancelable lock waiting."""
from contextlib import contextmanager
import fcntl
import os
import stat
import time

from .ai_common import AIFailure

PROVIDERS = frozenset({'codex_cli', 'gemini_api', 'claude_cli'})


@contextmanager
def provider_lock(settings, provider, check_current, *, wait_seconds=300):
    if provider not in PROVIDERS:
        raise AIFailure('AI_CONFIG_INVALID')
    root = settings.data_dir / 'ai-locks'
    directory = descriptor = None
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AIFailure('AI_LOCK_UNSAFE')
        descriptor = os.open(provider + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise AIFailure('AI_LOCK_UNSAFE')
        deadline = time.monotonic() + wait_seconds
        while True:
            check_current()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AIFailure('AI_PROVIDER_BUSY') from None
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
        check_current()
        yield
    except OSError:
        raise AIFailure('AI_LOCK_UNSAFE') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)

"""벽시계 보정과 PID 재사용에 독립적인 Linux 프로세스 식별자."""
from pathlib import Path


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def identity(pid):
    # comm에는 공백과 괄호가 들어갈 수 있으므로 마지막 닫는 괄호 뒤부터 읽는다.
    fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': int(pid), 'boot_id': boot_id(), 'start_ticks': int(fields[19])}


def matches(pid, recorded):
    if not isinstance(recorded, dict):
        return False
    try:
        return identity(pid) == recorded
    except (OSError, ValueError, IndexError):
        return False

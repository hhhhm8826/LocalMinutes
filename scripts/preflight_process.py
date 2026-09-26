"""Linux 준비 시험의 자식 프로세스까지 포함한 시간 제한."""
import os
import signal
import subprocess


def communicate_bounded(proc, text, timeout):
    try:
        stdout, stderr = proc.communicate(text, timeout=timeout)
        return stdout, stderr, False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return stdout, stderr, True

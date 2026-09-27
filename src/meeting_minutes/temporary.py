"""Attempt-scoped CLI scratch space, also removable after a killed child."""
from pathlib import Path
import re
import shutil


ROOT = Path('/tmp')


def attempt_prefix(job_id, attempt_id):
    if not all(re.fullmatch(r'[0-9a-f]{32}', value) for value in (job_id, attempt_id)):
        raise ValueError('Invalid attempt scope')
    return f'localminutes-codex-{job_id}-{attempt_id}-'


def cleanup_attempt_temporary(job_id, attempt_id):
    prefix = attempt_prefix(job_id, attempt_id)
    for path in ROOT.glob(prefix + '*'):
        # Never follow symlinks or remove another attempt's scratch space.
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != ROOT.resolve():
            continue
        shutil.rmtree(path)


def cleanup_job_temporary(job_id):
    """Used only after a job is terminal and its source retention has expired."""
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job scope')
    pattern = re.compile(r'localminutes-codex-' + re.escape(job_id) + r'-([0-9a-f]{32})-.+')
    for path in ROOT.glob(f'localminutes-codex-{job_id}-*'):
        match = pattern.fullmatch(path.name)
        if match:
            cleanup_attempt_temporary(job_id, match[1])

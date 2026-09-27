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

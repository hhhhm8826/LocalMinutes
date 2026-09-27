import subprocess
import sys
import time

from meeting_minutes.temporary import attempt_prefix, cleanup_attempt_temporary
from meeting_minutes.worker import Worker
from test_queue_media import context, register  # noqa: F401


def test_killed_child_scratch_removed_without_touching_other_attempt_or_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr('meeting_minutes.temporary.ROOT', tmp_path)
    job, attempt, other = 'a' * 32, 'b' * 32, 'c' * 32
    target = tmp_path / (attempt_prefix(job, attempt) + 'fixture')
    unrelated = tmp_path / (attempt_prefix(job, other) + 'fixture')
    unrelated.mkdir()
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'keep').write_text('keep')
    link = tmp_path / (attempt_prefix(job, attempt) + 'symlink')
    link.symlink_to(external, target_is_directory=True)
    code = ('from pathlib import Path; import sys,time; p=Path(sys.argv[1]); '
            'p.mkdir(); (p/"result.json").write_text("private fixture"); time.sleep(30)')
    process = subprocess.Popen([sys.executable, '-c', code, str(target)])
    try:
        deadline = time.monotonic() + 5
        while not (target / 'result.json').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (target / 'result.json').exists()
        process.kill()
        process.wait(timeout=5)
        cleanup_attempt_temporary(job, attempt)
        assert not target.exists()
        assert unrelated.exists() and link.is_symlink() and (external / 'keep').exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_worker_cleans_scratch_after_abnormal_child_exit(context, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr('meeting_minutes.temporary.ROOT', tmp_path)
    settings, repo = context
    register(context)
    job = repo.claim()
    target = tmp_path / (attempt_prefix(job['id'], job['attempt_id']) + 'crash')
    code = ('from pathlib import Path; import sys; p=Path(sys.argv[1]); '
            'p.mkdir(); (p/"result.json").write_text("private fixture"); sys.exit(1)')
    worker = Worker(settings, repo.engine, [sys.executable, '-c', code, str(target)])
    worker.execute(job)
    assert repo.job(job['id'])['state'] == 'FAILED'
    assert not target.exists()

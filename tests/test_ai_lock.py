import multiprocessing

import pytest

from meeting_minutes.ai_common import AIFailure
from meeting_minutes.ai_lock import provider_lock
from meeting_minutes.repository import Conflict
from test_queue_media import context  # noqa: F401


def hold(settings, pipe):
    with provider_lock(settings, 'codex_cli', lambda: None):
        pipe.send('locked')
        pipe.recv()


def test_provider_lock_cross_process_and_crash_recovery(context):  # noqa: F811
    settings, _ = context
    ctx = multiprocessing.get_context('fork')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=hold, args=(settings, child))
    process.start()
    try:
        assert parent.poll(5) and parent.recv() == 'locked'
        with pytest.raises(AIFailure, match='AI_PROVIDER_BUSY'):
            with provider_lock(settings, 'codex_cli', lambda: None, wait_seconds=.05):
                pytest.fail('same provider must serialize')
        with provider_lock(settings, 'gemini_api', lambda: None):
            pass
        process.kill()
        process.join(5)
        assert not process.is_alive()
        with provider_lock(settings, 'codex_cli', lambda: None, wait_seconds=.1):
            pass
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        parent.close()
        child.close()


def test_waiting_lock_checks_cancellation(context):  # noqa: F811
    settings, _ = context
    calls = []
    def cancelled():
        calls.append(True)
        if len(calls) == 2:
            raise Conflict('STALE_ATTEMPT')
    with provider_lock(settings, 'codex_cli', lambda: None):
        with pytest.raises(Conflict, match='STALE_ATTEMPT'):
            with provider_lock(settings, 'codex_cli', cancelled):
                pytest.fail('cancelled waiter cannot enter')
    assert len(calls) == 2


def test_lock_rejects_links_and_untrusted_permissions(context):  # noqa: F811
    settings, _ = context
    root = settings.data_dir / 'ai-locks'
    root.mkdir(mode=0o700)
    target = settings.data_dir / 'other'
    target.write_text('unchanged')
    (root / 'codex_cli.lock').symlink_to(target)
    with pytest.raises(AIFailure, match='AI_LOCK_UNSAFE'):
        with provider_lock(settings, 'codex_cli', lambda: None):
            pytest.fail('symlink must fail')
    assert target.read_text() == 'unchanged'
    root.chmod(0o755)
    with pytest.raises(AIFailure, match='AI_LOCK_UNSAFE'):
        with provider_lock(settings, 'gemini_api', lambda: None):
            pytest.fail('permissions must fail')

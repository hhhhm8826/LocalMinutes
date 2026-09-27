import os

from meeting_minutes.process_identity import identity, matches


def test_linux_identity_rejects_reused_pid_or_other_boot():
    current = identity(os.getpid())
    assert matches(os.getpid(), current)
    assert not matches(os.getpid(), current | {'start_ticks': current['start_ticks'] + 1})
    assert not matches(os.getpid(), current | {'boot_id': 'another-boot'})
    assert not matches(os.getpid(), None)

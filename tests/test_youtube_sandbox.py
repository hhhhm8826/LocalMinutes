import json
from pathlib import Path
import subprocess
import sys

import pytest

from meeting_minutes.settings import Settings
from meeting_minutes.youtube_sandbox import sandbox_command


@pytest.mark.parametrize('linked_environment', [False, True])
def test_real_mount_namespace_hides_home_and_blocks_source_writes(tmp_path, monkeypatch, linked_environment):
    default = Path('/usr/bin/bwrap')
    local = Path.home() / '.local/share/local-meeting-minutes/toolchain/bubblewrap/unpacked/usr/bin/bwrap'
    executable = default if default.is_file() else local
    settings = Settings(youtube_bwrap=executable)
    if not executable.is_file() or not settings.youtube_deno.is_file():
        pytest.skip('prepared bwrap/Deno required; doctor records missing dependency')
    private = tmp_path / 'private-token'
    private.write_text('fixture-not-a-real-secret')
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    if linked_environment:
        alias = tmp_path / 'linked-venv'
        alias.symlink_to(Path(sys.prefix).resolve(), target_is_directory=True)
        monkeypatch.setattr(sys, 'prefix', str(alias))
        monkeypatch.setattr(sys, 'executable', str(alias / 'bin' / Path(sys.executable).name))
    source = Path(__file__).resolve().parents[1] / 'src/meeting_minutes/__init__.py'
    script = '''import json,os,pathlib,subprocess
private,source,scratch,deno=map(pathlib.Path,__import__('sys').argv[1:])
assert not private.exists()
assert not os.environ.get('HTTP_PROXY') and not os.environ.get('MINUTES_CODEX_HOME')
try:
    source.open('a')
except OSError:
    pass
else:
    raise AssertionError('source writable')
(scratch/'ok').write_text('ok')
print(json.dumps({'isolation':'PASS','deno':subprocess.check_output([str(deno),'--version'],text=True).splitlines()[0]}))
'''
    command = sandbox_command(settings, scratch, [sys.executable, '-c', script, private, source, scratch, settings.youtube_deno])
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, env={'PATH': '/usr/bin:/bin'})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['isolation'] == 'PASS'
    assert (scratch / 'ok').read_text() == 'ok'

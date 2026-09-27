import importlib.util
from pathlib import Path
import subprocess


def test_worktree_index_and_windows_checkout(tmp_path):
    spec = importlib.util.spec_from_file_location('line_endings', Path(__file__).resolve().parents[1] / 'scripts/check-line-endings.py')
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    def git(*args, **kwargs):
        return subprocess.check_output(['git', *args], cwd=tmp_path, **kwargs)
    git('init', '-q')
    git('config', 'core.autocrlf', 'true')
    (tmp_path / '.gitattributes').write_bytes(b'* text=auto eol=lf\n')
    sample = tmp_path / 'sample.md'
    sample.write_bytes(b'one\r\ntwo\r\n')
    (tmp_path / 'binary.bin').write_bytes(b'\0\r\n')
    assert check.audit(tmp_path)['findings'][0]['path'] == 'sample.md'
    git('add', '.')
    assert check.audit(tmp_path, index=True)['status'] == 'PASS'
    git('-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture')
    sample.unlink()
    git('checkout', '--', 'sample.md')
    assert sample.read_bytes() == b'one\ntwo\n'
    assert check.audit(tmp_path)['status'] == 'PASS'
    # Bypass normal add filters to prove CI checks the actual staged bytes too.
    blob = git('hash-object', '-w', '--stdin', input=b'one\r\ntwo\n').decode().strip()
    git('update-index', '--cacheinfo', '100644,' + blob + ',sample.md')
    assert check.audit(tmp_path, index=True)['status'] == 'FAIL'
    assert check.violations(b'one\rtwo') == 1

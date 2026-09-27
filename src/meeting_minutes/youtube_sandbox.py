"""Build a minimal acquisition mount namespace without user data or auth homes."""
from pathlib import Path
import sys
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, version

from .processes import ProcessFailure


def sandbox_command(settings, scratch, command):
    scratch = scratch.resolve(strict=True)
    deno = settings.youtube_deno.resolve(strict=True)
    bwrap = settings.youtube_bwrap
    if not bwrap.is_file() or not scratch.is_dir():
        raise ProcessFailure('YOUTUBE_ISOLATION_REQUIRED')
    package_root = Path(__file__).resolve().parents[1]
    environment_root = Path(sys.prefix).resolve()
    child_command = list(map(str, command))
    if child_command and child_command[0] == sys.executable:
        # The namespace mounts the real venv, not checkout-specific symlink
        # aliases. Keep the venv interpreter path (not its /usr target).
        child_command[0] = str(environment_root / 'bin' / Path(sys.executable).name)
    runtime = scratch / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    argv = [str(bwrap), '--die-with-parent', '--unshare-user', '--unshare-pid', '--unshare-ipc',
            '--unshare-uts', '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev',
            '--bind', str(runtime), '/tmp', '--dir', '/home', '--dir', '/etc']
    for path in ['/etc/ssl', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf']:
        if Path(path).exists():
            argv.extend(['--ro-bind', path, path])
    for path in [environment_root, package_root, deno]:
        argv.extend(['--ro-bind', str(path), str(path)])
    argv.extend(['--bind', str(scratch), str(scratch), '--chdir', str(scratch),
        '--setenv', 'HOME', '/tmp', '--setenv', 'TMPDIR', '/tmp', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'PYTHONPATH', str(package_root), '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
        '--setenv', 'DENO_NO_UPDATE_CHECK', '1', '--setenv', 'DENO_DIR', '/tmp/deno',
        '--', *child_command])
    return argv


def youtube_readiness(settings):
    checks = {}
    for package, expected in [('yt-dlp', '2026.8.19'), ('yt-dlp-ejs', '0.8.0')]:
        try:
            checks[package] = version(package) == expected
        except PackageNotFoundError:
            checks[package] = False
    checks['deno'] = checks['mount_isolation'] = False
    try:
        with tempfile.TemporaryDirectory(prefix='minutes-isolation-') as temporary:
            command = sandbox_command(settings, Path(temporary), [settings.youtube_deno, '--version'])
            result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                    env={'PATH': '/usr/bin:/bin'})
            checks['mount_isolation'] = result.returncode == 0
            checks['deno'] = result.returncode == 0 and result.stdout.splitlines()[0].startswith('deno 2.9.7 ')
    except (OSError, ProcessFailure, subprocess.TimeoutExpired, IndexError):
        pass
    return {'checks': checks, 'ready': all(checks.values()), 'network_tested': False}

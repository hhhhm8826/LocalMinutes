"""Install the pinned Deno release with a fixed upstream SHA-256."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request
import zipfile

VERSION = '2.9.7'
SHA256 = 'c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490'
URL = f'https://github.com/denoland/deno/releases/download/v{VERSION}/deno-x86_64-unknown-linux-gnu.zip'


def main():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise SystemExit('Linux x86_64 일반 사용자로 실행하세요.')
    root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'local-meeting-minutes/toolchain'
    if not root.is_absolute():
        raise SystemExit('XDG_DATA_HOME must be absolute')
    root.mkdir(parents=True, exist_ok=True)
    if not shutil.which('bwrap'):
        package_root = root / 'bubblewrap'
        package_root.mkdir(exist_ok=True)
        subprocess.run(['apt-get', 'download', 'bubblewrap=0.9.0-1ubuntu0.3'], cwd=package_root, check=True, timeout=120)
        package = package_root / 'bubblewrap_0.9.0-1ubuntu0.3_amd64.deb'
        subprocess.run(['dpkg-deb', '-x', str(package), str(package_root / 'unpacked')], check=True, timeout=30)
    archive = root / f'deno-{VERSION}.zip'
    if not archive.exists():
        partial = archive.with_suffix('.part')
        with urllib.request.urlopen(URL, timeout=30) as response, partial.open('wb') as stream:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100_000_000:
                    raise RuntimeError('Deno archive size limit')
                stream.write(chunk)
        partial.replace(archive)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SHA256:
            raise RuntimeError('Deno checksum mismatch')
    target = root / f'deno-{VERSION}'
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        if package.namelist() != ['deno']:
            raise RuntimeError('Unexpected Deno archive contents')
        binary = target / 'deno'
        binary.write_bytes(package.read('deno'))
        binary.chmod(0o700)
    subprocess.run([str(binary), '--version'], check=True, timeout=15,
                   env={'PATH': '/usr/bin:/bin', 'DENO_NO_UPDATE_CHECK': '1'})


if __name__ == '__main__':
    main()

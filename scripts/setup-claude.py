"""Optional pinned Claude runtime installation; no login or generation."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.request

VERSION = '2.1.283'
URL = 'https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.283.tgz'
INTEGRITY = 'q9+Ke42t/I6oLb/bTmh6sI24jMw4KNwz3QbQLp9dVQ8ipQChfAW5L11SX8jcDzVCW/P2tId7ftI+lgBURvz9uQ=='


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'local-meeting-minutes/toolchain'
    target = root / ('claude-' + VERSION) / 'claude'
    if not args.install:
        print(json.dumps({'version':VERSION,'target':str(target),'maximum_download_bytes':300*1024**2,
                          'minimum_free_bytes':1024**3,'system_changes':False,'login':False,'generation':False}))
        return
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0 or not root.is_absolute():
        raise SystemExit('Linux x86_64 non-root absolute user toolchain required')
    if any(path.is_symlink() for path in (root, *root.parents, target.parent, target)):
        raise SystemExit('Symlink toolchain rejected')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        raise SystemExit('Pinned target already exists; inspect it instead of overwriting')
    if shutil.disk_usage(root).free < 1024**3:
        raise SystemExit('At least 1 GiB free space required')
    with tempfile.TemporaryDirectory(prefix='claude-install-', dir=root) as directory:
        archive = Path(directory) / 'download.tgz'
        total, checksum = 0, hashlib.sha512()
        with urllib.request.urlopen(URL, timeout=30) as response, archive.open('wb') as output:
            if response.url != URL:
                raise SystemExit('Unexpected download redirect')
            while block := response.read(1024**2):
                total += len(block)
                if total > 300*1024**2:
                    raise SystemExit('Archive download budget exceeded')
                checksum.update(block)
                output.write(block)
        if base64.b64encode(checksum.digest()).decode() != INTEGRITY:
            raise SystemExit('Claude archive integrity mismatch')
        executable = Path(directory) / 'claude'
        with tarfile.open(archive) as archive_file:
            candidates = []
            for item in archive_file.getmembers():
                if item.isfile() and 0 < item.size < 300*1024**2:
                    with archive_file.extractfile(item) as source:
                        if source.read(4) == b'\x7fELF':
                            candidates.append(item)
            if len(candidates) != 1:
                raise SystemExit('Expected exactly one native ELF executable')
            with archive_file.extractfile(candidates[0]) as source, executable.open('wb') as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
        executable.chmod(0o700)
        target.parent.mkdir(mode=0o700)
        executable.replace(target)
        manifest = {'version':VERSION,'source':URL,'archive_sha512':INTEGRITY,
                    'binary_sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest()}
        (target.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'installed':str(target),'version':VERSION,'downloaded_bytes':total}))


if __name__ == '__main__':
    main()

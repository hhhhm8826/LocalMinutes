"""Acquisition namespace entry point. Emits only bounded structured status."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import resource
import sys

from .processes import ProcessFailure
from .youtube_acquisition import acquire


def main():
    url, directory, deno = sys.argv[1:]
    resource.setrlimit(resource.RLIMIT_FSIZE, (2_000_000_000, 2_000_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    try:
        protocol = sys.stdout
        # Downloader progress is untrusted text, never part of our JSON protocol.
        with redirect_stdout(sys.stderr):
            result = acquire(url, Path(directory), Path(deno),
                             stage=lambda stage: print(json.dumps({'stage': stage}), file=protocol, flush=True))
        print(json.dumps({'result': result}), flush=True)
    except Exception as exc:
        code = exc.code if isinstance(exc, ProcessFailure) else 'YOUTUBE_ACCESS_FAILED'
        print(json.dumps({'error': code}), flush=True)
        raise SystemExit(2)


if __name__ == '__main__':
    main()

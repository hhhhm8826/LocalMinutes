"""항상 루프백에만 바인딩한다."""
import os
import argparse
import fcntl
import json
import signal
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
import psutil

from .api import create_app
from .settings import Settings
from .process_identity import identity


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    settings = Settings(**({'data_dir': args.data_dir} if args.data_dir else {}))
    settings.prepare()
    target = urlsplit(settings.origin)
    with (settings.data_dir / 'app.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('이미 실행 중인 앱이 있습니다.')
        record = settings.data_dir / 'app-process.json'
        record.write_text(json.dumps({'pid': os.getpid(), 'created_at': psutil.Process().create_time(),
                                      'identity': identity(os.getpid()),
                                      'data_dir': str(settings.data_dir)}))
        try:
            # Uvicorn은 정상 정리 후 SIGTERM을 재전달한다. 바깥 finally도 실행되게 한다.
            signal.signal(signal.SIGTERM, lambda *_: None)
            uvicorn.run(create_app(settings), host=target.hostname, port=target.port or 80,
                        access_log=False, proxy_headers=False)
        finally:
            record.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

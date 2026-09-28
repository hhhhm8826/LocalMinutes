"""Import a local owner-supplied key without echoing it or placing it in argv."""
import argparse
import json
from pathlib import Path
import re

from meeting_minutes.ai_policy import invalidate_key_status
from meeting_minutes.ai_secrets import GeminiSecretStore, SecretStoreError
from meeting_minutes.repository import Repository
from meeting_minutes.settings import Settings
from meeting_minutes.storage import make_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--config-dir', required=True, type=Path)
    parser.add_argument('--data-dir', required=True, type=Path)
    args = parser.parse_args()
    settings = Settings(config_dir=args.config_dir, data_dir=args.data_dir)
    try:
        if args.source.is_symlink() or not args.source.is_file() or args.source.stat().st_size > 8192:
            raise SecretStoreError('AI_KEY_IMPORT_FILE_INVALID')
        raw = args.source.read_text(encoding='utf-8-sig').strip()
        lines = [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if len(lines) != 1:
            raise SecretStoreError('AI_KEY_IMPORT_FORMAT_INVALID')
        key = lines[0]
        if '=' in key or ':' in key:
            match = re.fullmatch(r'(?i)(?:gemini[_ -]?|google[_ -]?)?(?:api[_ -]?)?key\s*[:=]\s*(.+)', key)
            if not match:
                raise SecretStoreError('AI_KEY_IMPORT_FORMAT_INVALID')
            key = match.group(1).strip()
        if key[:1] in ('"', "'") and key[-1:] == key[:1]:
            key = key[1:-1]
        store = GeminiSecretStore(settings)
        current = store.status()
        status = store.replace(key, current['credential_revision'])
        # Source remains owner-managed and is explicitly excluded from all project exports.
        if settings.database_path.exists():
            engine = make_engine(settings.database_path)
            try:
                from sqlalchemy import inspect
                if 'ai_readiness' in inspect(engine).get_table_names():
                    invalidate_key_status(Repository(engine))
            finally:
                engine.dispose()
        print(json.dumps({'registered':status['registered'], 'source_retained':True, 'key_printed':False}))
    except (SecretStoreError, UnicodeError, OSError):
        raise SystemExit('AI_KEY_IMPORT_FAILED: local file format/path or secret-store validation failed; value omitted') from None


if __name__ == '__main__':
    main()

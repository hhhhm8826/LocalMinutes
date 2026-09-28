import concurrent.futures
import json
import multiprocessing
import os
import signal

import pytest

from meeting_minutes.ai_secrets import GeminiSecretStore, SecretStoreError
from meeting_minutes.settings import Settings


@pytest.fixture
def store(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', config_dir=tmp_path / 'config', cache_dir=tmp_path / 'cache')
    settings.prepare()
    return GeminiSecretStore(settings)


def test_replace_restart_delete_and_no_reexposure(store):
    first = store.replace('canary-first', None)
    second = store.replace('canary-second', first['credential_revision'])
    assert first['credential_revision'] != second['credential_revision']
    current = store.read()
    assert current.key.get_secret_value() == 'canary-second'
    assert 'canary' not in repr(current) and 'canary' not in json.dumps(second)
    folder = store.config / '.apikey'
    assert {p.name for p in folder.iterdir()} == {'gemini.json', '.lock'}
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir())
    assert store.status() == second
    store.delete(second['credential_revision'])
    assert not store.status()['registered']
    assert not (folder / 'gemini.json').exists()


@pytest.mark.parametrize('invalid', ['', 'x y', 'x\n', '\x00', 'x' * 4097, None])
def test_invalid_does_not_damage_prior(store, invalid):
    status = store.replace('canary-original', None)
    with pytest.raises(SecretStoreError, match='AI_KEY_INPUT_INVALID'):
        store.replace(invalid, status['credential_revision'])
    assert store.status() == status


def test_concurrent_revision_and_io_failure(store, monkeypatch):
    first = store.replace('canary-original', None)
    def replace(number):
        try:
            store.replace('canary-' + str(number), first['credential_revision'])
            return True
        except SecretStoreError as exc:
            assert exc.code == 'AI_CREDENTIAL_REVISION_CONFLICT'
            return False
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        assert sum(executor.map(replace, [1, 2])) == 1
    before = store.status()
    def fail(*args, **kwargs):
        raise OSError('untrusted error must not escape')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(SecretStoreError, match='^AI_SECRET_IO_FAILED$'):
        store.replace('canary-failure', before['credential_revision'])
    assert store.status() == before
    assert not list((store.config / '.apikey').glob('*.tmp'))


@pytest.mark.parametrize('attack', ['directory_link', 'file_link', 'hardlink', 'fifo', 'permissions'])
def test_reject_unsafe_files(store, tmp_path, attack):
    folder = store.config / '.apikey'
    if attack == 'directory_link':
        folder.symlink_to(tmp_path, target_is_directory=True)
    else:
        store.replace('canary-original', None)
        path = folder / 'gemini.json'
        if attack == 'permissions':
            path.chmod(0o644)
        elif attack == 'hardlink':
            os.link(path, folder / 'second')
        else:
            path.unlink()
            if attack == 'file_link':
                path.symlink_to(tmp_path / 'target')
            else:
                os.mkfifo(path, 0o600)
    with pytest.raises(SecretStoreError):
        store.read()


def test_interrupted_write_cleanup(store):
    before = store.replace('canary-original', None)
    read, write = multiprocessing.Pipe(duplex=False)
    def interrupted():
        with store.locked() as directory:
            fd = os.open('.gemini-interrupted.tmp', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
            os.write(fd, b'partial')
            write.send(True)
            signal.pause()
    child = multiprocessing.get_context('fork').Process(target=interrupted)
    child.start()
    try:
        assert read.poll(5) and read.recv()
    finally:
        child.kill()
        child.join(5)
        read.close()
        write.close()
    assert child.exitcode is not None
    assert store.status() == before
    assert not list((store.config / '.apikey').glob('*.tmp'))


def test_public_path_rejected(tmp_path):
    settings = Settings(data_dir=tmp_path, config_dir=tmp_path / 'media/config', cache_dir=tmp_path / 'cache')
    with pytest.raises(SecretStoreError, match='AI_SECRET_PATH_UNSAFE'):
        GeminiSecretStore(settings)

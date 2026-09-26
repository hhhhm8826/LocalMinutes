"""Windows queue transport: persist accepted review evidence before deleting exact ACKs.

Run --accept-result ID only AFTER the master has handled the review verdict.
Run without it at review checkpoints to drain ACKs for already handled results.
Never interprets an ACK as a review verdict or executes its supplied path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def legacy_review(root, identifier):
    path = root / 'legacy-review-ids.json'
    return path.exists() and identifier in read(path)['request_ids']


def evidence(root, identifier, master):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identifier):
        raise ValueError('invalid request ID')
    folder = (root / 'reviews' / identifier).resolve()
    if folder.parent != (root / 'reviews').resolve():
        raise ValueError('review outside workflow')
    request, result = read(folder / 'request.json'), read(folder / 'result.json')
    if not (request['request_id'] == result['request_id'] == identifier
            and request['reply_to_thread_id'] == master
            and request['head_commit'] == result['head_commit']
            and (result['verdict'] in ('PASS', 'CHANGES_REQUIRED', 'BLOCKED')
                 or (result['verdict'] == 'FAIL' and legacy_review(root, identifier)))):
        raise ValueError('review identity/commit/verdict mismatch')
    return {'result_sha256': hashlib.sha256((folder / 'result.json').read_bytes()).hexdigest(),
            'head_commit': result['head_commit'], 'verdict': result['verdict']}


class RPC:
    def __init__(self, cli):
        self.process = subprocess.Popen([cli, 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8')
        self.lines, self.counter = queue.Queue(), 0
        def reader():
            for line in self.process.stdout:
                self.lines.put(line)
        threading.Thread(target=reader, daemon=True).start()

    def call(self, method, params):
        self.counter += 1
        self.process.stdin.write(json.dumps({'id': self.counter, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                value = json.loads(self.lines.get(timeout=1))
            except queue.Empty:
                continue
            if value.get('id') == self.counter:
                if 'error' in value:
                    raise RuntimeError(value['error'])
                return value['result']
        raise TimeoutError(method)

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def listing(rpc, master):
    items, cursor = [], None
    while True:
        params = {'threadId': master, 'limit': 100}
        if cursor:
            params['cursor'] = cursor
        page = rpc.call('thread/queue/list', params)
        items.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            return items


def drain(rpc, root, master, ledger, ledger_path):
    candidates = []
    for item in listing(rpc, master):
        inputs = item.get('input', [])
        if len(inputs) != 1 or inputs[0].get('type') != 'text':
            continue
        message = inputs[0].get('text', '')
        match = re.fullmatch(r'VERIFY_RESULT id=([a-zA-Z0-9_-]{1,64}); commit=([0-9a-f]{40}); result=([^\r\n]+)', message)
        if match:
            identifier, announced_commit, supplied = match.groups()
        else:
            # Keep old preflight receipts consumable without rewriting history.
            match = re.fullmatch(r'(?:PREFLIGHT|VERIFY)_ACK id=([a-zA-Z0-9_-]{1,64}); (?:duplicate=reused; )?result=([^\r\n]+)', message)
            if not match:
                continue
            identifier, supplied = match.groups()
            if not legacy_review(root, identifier):
                continue
            announced_commit = None
        accepted = ledger['accepted'].get(identifier)
        expected = root / 'reviews' / identifier / 'result.json'
        if not accepted or not Path(supplied).is_absolute() or Path(supplied).resolve() != expected.resolve():
            continue
        if announced_commit is not None and announced_commit != accepted['head_commit']:
            continue
        if evidence(root, identifier, master) != accepted:
            raise ValueError('accepted result changed; refusing deletion')
        candidates.append(item['id'])
        ledger['messages'][item['id']] = {'request_id': identifier, 'status': 'DELETE_PENDING'}
        save(ledger_path, ledger)
        reply = rpc.call('thread/queue/delete', {'threadId': master, 'queuedSubmissionId': item['id']})
        ledger['messages'][item['id']]['status'] = 'DELETED' if reply['deleted'] else 'ALREADY_ABSENT'
        save(ledger_path, ledger)
    remaining = {item['id'] for item in listing(rpc, master)}
    if remaining.intersection(candidates):
        raise RuntimeError('ACK still queued')
    # A prior delete may have committed before its response was lost. Absence
    # proves it is no longer pending, but does not prove who removed it.
    for identifier, record in ledger['messages'].items():
        if record['status'] == 'DELETE_PENDING' and identifier not in remaining:
            request_id = record['request_id']
            if evidence(root, request_id, master) != ledger['accepted'][request_id]:
                raise ValueError('accepted result changed; refusing reconciliation')
            record['status'] = 'ABSENT_ON_RECHECK'
    save(ledger_path, ledger)
    return {'handled_this_run': len(candidates), 'remaining_queue_count': len(remaining)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--accept-result', action='append', default=[])
    args = parser.parse_args()
    registry = read(args.registry)
    root = Path(registry['shared_workflow_root']).resolve(strict=True)
    if args.registry.resolve().parent != root:
        raise ValueError('registry outside workflow')
    master = registry['master']['thread_id']
    path = root / 'ack-consumption.json'
    # Exclusive OS file creation prevents concurrent ledger writers. Crash leaves
    # a visible lock requiring owner inspection; it never permits unsafe deletion.
    lock = root / 'ack-consumption.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    rpc = None
    try:
        ledger = read(path) if path.exists() else {'thread_id': master, 'accepted': {}, 'messages': {}}
        if ledger['thread_id'] != master:
            raise ValueError('ledger thread mismatch')
        for identifier in args.accept_result:
            current = evidence(root, identifier, master)
            if identifier in ledger['accepted'] and ledger['accepted'][identifier] != current:
                raise ValueError('accepted evidence changed')
            ledger['accepted'][identifier] = current
        save(path, ledger)
        rpc = RPC(registry['queue_cli'])
        rpc.call('initialize', {'clientInfo': {'name': 'localminutes-ack-consumer', 'version': '1.0'},
                               'capabilities': {'experimentalApi': True}})
        rpc.process.stdin.write('{"method":"initialized"}\n')
        rpc.process.stdin.flush()
        print(json.dumps(drain(rpc, root, master, ledger, path)))
    finally:
        if rpc:
            rpc.close()
        os.close(fd)
        lock.unlink()


if __name__ == '__main__':
    main()

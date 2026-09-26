"""Focused transport tests; no model calls or real queue writes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('consumer', Path(__file__).with_name('consume-review-acks.py'))
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


class FakeRPC:
    def __init__(self, items, fail=False, lose_response=False):
        self.items, self.fail, self.deleted = items, fail, []
        self.lose_response = lose_response

    def call(self, method, params):
        if method.endswith('/list'):
            return {'data': self.items, 'nextCursor': None}
        if self.fail:
            raise TimeoutError('simulated ambiguous delete')
        self.deleted.append(params['queuedSubmissionId'])
        self.items = [item for item in self.items if item['id'] != params['queuedSubmissionId']]
        if self.lose_response:
            raise TimeoutError('server deleted but response lost')
        return {'deleted': True}


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    folder = root / 'reviews' / 'r1'
    folder.mkdir(parents=True)
    consumer.save(root / 'legacy-review-ids.json', {'request_ids': ['r1']})
    consumer.save(folder / 'request.json', {'request_id': 'r1', 'head_commit': 'abc', 'reply_to_thread_id': 'master'})
    result = {'request_id': 'r1', 'head_commit': 'abc', 'verdict': 'FAIL'}
    consumer.save(folder / 'result.json', result)
    ledger = {'accepted': {'r1': consumer.evidence(root, 'r1', 'master')}, 'messages': {}}
    path = root / 'ledger.json'
    def item(identifier, text):
        return {'id': identifier, 'input': [{'type': 'text', 'text': text}]}
    ack = item('ack', f'VERIFY_ACK id=r1; result={folder / "result.json"}')
    user = item('user', 'Please continue my task')
    unknown = item('unknown', f'VERIFY_ACK id=r2; result={folder / "result.json"}')
    wrong_path = item('wrong', 'VERIFY_ACK id=r1; result=/unrelated/result.json')
    rpc = FakeRPC([ack, user, unknown, wrong_path])
    assert consumer.drain(rpc, root, 'master', ledger, path)['handled_this_run'] == 1
    assert rpc.deleted == ['ack'] and len(rpc.items) == 3
    assert consumer.drain(rpc, root, 'master', ledger, path)['handled_this_run'] == 0
    rpc = FakeRPC([ack], fail=True)
    try:
        consumer.drain(rpc, root, 'master', ledger, path)
        raise AssertionError('expected timeout')
    except TimeoutError:
        assert consumer.read(path)['messages']['ack']['status'] == 'DELETE_PENDING'
    rpc.fail = False
    consumer.drain(rpc, root, 'master', ledger, path)
    # Server deletes successfully, but the response (or client) is lost.
    rpc = FakeRPC([ack, user], lose_response=True)
    try:
        consumer.drain(rpc, root, 'master', ledger, path)
        raise AssertionError('expected lost response')
    except TimeoutError:
        assert consumer.read(path)['messages']['ack']['status'] == 'DELETE_PENDING'
    recovered = consumer.read(path)
    rpc.lose_response = False
    consumer.drain(rpc, root, 'master', recovered, path)
    assert consumer.read(path)['messages']['ack']['status'] == 'ABSENT_ON_RECHECK'
    assert rpc.deleted == ['ack'] and rpc.items == [user]
    result['verdict'] = 'PASS'
    consumer.save(folder / 'result.json', result)
    rpc = FakeRPC([ack])
    try:
        consumer.drain(rpc, root, 'master', ledger, path)
        raise AssertionError('expected changed evidence rejection')
    except ValueError:
        assert rpc.deleted == []
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    folder = root / 'reviews' / 'R1-001'
    folder.mkdir(parents=True)
    sha = 'a' * 40
    consumer.save(folder / 'request.json', {'request_id': 'R1-001', 'head_commit': sha, 'reply_to_thread_id': 'master'})
    for verdict in ('PASS', 'CHANGES_REQUIRED', 'BLOCKED'):
        consumer.save(folder / 'result.json', {'request_id': 'R1-001', 'head_commit': sha, 'verdict': verdict})
        ledger = {'accepted': {'R1-001': consumer.evidence(root, 'R1-001', 'master')}, 'messages': {}}
        canonical = item('canonical', f'VERIFY_RESULT id=R1-001; commit={sha}; result={folder / "result.json"}')
        stale = item('stale', f'VERIFY_RESULT id=R1-001; commit={"b" * 40}; result={folder / "result.json"}')
        malformed = item('malformed', f'VERIFY_RESULT id=R1-001; commit=abc; result={folder / "result.json"}')
        legacy = item('legacy', f'VERIFY_ACK id=R1-001; result={folder / "result.json"}')
        relative = item('relative', f'VERIFY_RESULT id=R1-001; commit={sha}; result={os.path.relpath(folder / "result.json")}')
        rpc = FakeRPC([canonical, stale, malformed, legacy, relative, user])
        consumer.drain(rpc, root, 'master', ledger, root / 'ledger.json')
        assert rpc.deleted == ['canonical'] and rpc.items == [stale, malformed, legacy, relative, user]
    consumer.save(folder / 'result.json', {'request_id': 'R1-001', 'head_commit': sha, 'verdict': 'FAIL'})
    try:
        consumer.evidence(root, 'R1-001', 'master')
        raise AssertionError('new FAIL must be rejected')
    except ValueError:
        pass
print(json.dumps({'status': 'PASS', 'checks': ['accepted legacy FAIL consumed', 'unrelated/unaccepted/path mismatch retained',
    'repeat no-op', 'timeout receipt persists and retry succeeds', 'lost response reconciled after restart', 'changed result rejected',
    'VERIFY_RESULT supports all three plan verdicts', 'stale and malformed notification SHA preserved',
    'legacy allowed only for explicit historical IDs', 'relative result path retained', 'new FAIL rejected']}))

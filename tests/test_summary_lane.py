import threading
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.minutes_management import queue_generation
from meeting_minutes.worker import Worker
from test_minutes_management import ready
from test_queue_media import context, register  # noqa: F401


def setup_lanes(context):  # noqa: F811
    settings, repo = context
    meeting, version = ready(context)
    register(context)
    summary = queue_generation(repo,settings,meeting['id'],GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version,allow_external_text=True,new_draft=True),'independent-summary')
    return settings,repo,summary


def test_lane_claims_do_not_block_each_other(context):  # noqa: F811
    _,repo,summary = setup_lanes(context)
    analysis = repo.claim('analysis')
    assert repo.claim('summary')['id'] == summary['id']
    assert repo.claim('analysis') is None and repo.claim('summary') is None
    repo.cancel(summary['id'])
    assert repo.job(analysis['id'])['state'] == 'RUNNING'
    repo.finish(summary['id'],summary['attempt_id'],'CANCELLED')
    repo.finish(analysis['id'],analysis['attempt_id'],'BLOCKED','fixture')
    repo.retry(summary['id'])
    assert repo.claim('summary')['id'] == summary['id']
    assert repo.claim('analysis') is None


def test_worker_runs_two_lanes_concurrently_without_models(context):  # noqa: F811
    settings,repo,_ = setup_lanes(context)
    worker = Worker(settings,repo.engine)
    barrier = threading.Barrier(2, timeout=3)
    seen = []
    def execute(job):
        seen.append(job['kind'])
        barrier.wait()
        repo.finish(job['id'],job['attempt_id'],'COMPLETED')
        worker.stopping = True
    worker.execute = execute
    assert worker.run()
    assert sorted(seen) == ['summarize','transcribe']

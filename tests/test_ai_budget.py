from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from meeting_minutes.ai_budget import BudgetUpdate, get_status, reserve, settle, token_total, update, week
from meeting_minutes.gemini_budget import RequestBudgetUpdate
from meeting_minutes.ai_common import AIFailure
from meeting_minutes.api import create_app
from meeting_minutes.repository import Conflict
from test_queue_media import context, register  # noqa: F401


def test_week_is_kst_monday_not_utc_or_rolling_window():
    sunday = datetime(2026, 9, 27, 23, 59, 59, tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
    assert week(sunday)[1] == sunday + 1
    assert week(sunday+1)[0] == sunday+1
    assert week(sunday+1)[1] - week(sunday+1)[0] == 7*86400


@pytest.mark.parametrize('provider,usage,total', [
    ('codex_cli',{'input_tokens':100,'cached_input_tokens':70,'output_tokens':20},120),
    ('claude_cli',{'input_tokens':10,'cache_creation_input_tokens':20,'cache_read_input_tokens':30,'output_tokens':40},100),
    ('gemini_api',{'total_token_count':150,'prompt_token_count':100,'candidates_token_count':20,'thoughts_token_count':30},150),
    ('codex_cli',{'input_tokens':True,'output_tokens':20},None),
])
def test_usage_normalization_without_double_count(provider,usage,total):
    assert token_total(provider,usage) == total


def test_one_call_can_overshoot_then_blocks_and_limit_raise_resumes(context):  # noqa: F811
    _,repo=context
    update(repo,BudgetUpdate(expected_revision=1,token_limit=100))
    with repo.write() as c:
        reserve(c,'call','codex_cli',200)
        settle(c,'call',{'input_tokens':80,'output_tokens':90})
        settle(c,'call',{'input_tokens':1,'output_tokens':1})
    assert get_status(repo)['used_tokens'] == 170
    with pytest.raises(AIFailure,match='AI_WEEKLY_BUDGET_EXHAUSTED'),repo.write() as c:
        reserve(c,'blocked','codex_cli',10)
    update(repo,BudgetUpdate(expected_revision=2,token_limit=200))
    with repo.write() as c:
        reserve(c,'next','codex_cli',10)
    assert get_status(repo)['reserved_tokens'] == 10
    with pytest.raises(Conflict,match='AI_BUDGET_REVISION_CONFLICT'):
        update(repo,BudgetUpdate(expected_revision=2,token_limit=300))


def test_unknown_call_is_not_refunded_and_week_refills(context,monkeypatch):  # noqa: F811
    _,repo=context
    now=datetime(2026,9,27,23,59,59,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
    monkeypatch.setattr('meeting_minutes.ai_budget.time.time',lambda:now)
    with repo.write() as c:
        reserve(c,'crashed','codex_cli',20000001)
        settle(c,'crashed',{})
    assert get_status(repo)['exhausted']
    now+=1
    assert get_status(repo)['remaining_tokens'] == 20000000
    with repo.engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM ai_budget_ledger')).scalar_one() == 1


@pytest.mark.parametrize('resume_by',['increase','week'])
def test_budget_head_keeps_order_resumes_and_can_be_cancelled(context,monkeypatch,resume_by):  # noqa: F811
    _,repo=context
    now=datetime(2026,9,27,23,59,59,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
    monkeypatch.setattr('meeting_minutes.ai_budget.time.time',lambda:now)
    register(context)
    job=repo.claim('analysis')
    with repo.write() as c:
        reserve(c,'spent','codex_cli',20000000)
    repo.finish(job['id'],job['attempt_id'],'BLOCKED','AI_WEEKLY_BUDGET_EXHAUSTED')
    assert repo.claim('analysis') is None
    if resume_by == 'increase':
        update(repo,BudgetUpdate(expected_revision=1,token_limit=30000000))
    else:
        now+=1
    resumed=repo.claim('analysis')
    assert resumed['id'] == job['id'] and resumed['attempt_id'] != job['attempt_id']
    assert resumed['ai_config_json'] == job['ai_config_json']
    repo.finish(resumed['id'],resumed['attempt_id'],'BLOCKED','AI_WEEKLY_BUDGET_EXHAUSTED')
    repo.cancel(resumed['id'])
    assert repo.claim('analysis') is None


def test_budget_endpoint_owner_csrf_and_revision(context):  # noqa: F811
    settings,repo=context
    with TestClient(create_app(settings),base_url=settings.origin) as client:
        assert client.get('/api/settings/ai/budget/codex_cli').status_code == 401
        body={'expected_revision':1,'token_limit':25000000}
        assert client.patch('/api/settings/ai/budget/codex_cli',json=body).status_code in (401,403)
        auth = client.post('/api/auth/login',headers={'origin':settings.origin},json={'key':settings.owner_key_path.read_text()})
        headers = {'origin':settings.origin, 'x-csrf-token':auth.json()['csrf_token']}
        state=client.get('/api/settings/ai/budget/codex_cli')
        assert state.status_code == 200 and state.json()['token_limit'] == 20000000
        assert state.headers['cache-control'] == 'no-store'
        assert client.patch('/api/settings/ai/budget/codex_cli',json=body).status_code == 403
        assert client.patch('/api/settings/ai/budget/codex_cli',headers=headers,json=body).status_code == 200
        assert client.patch('/api/settings/ai/budget/codex_cli',headers=headers,json=body).status_code == 409



def test_real_call_boundary_settles_before_next_call_and_preserves_result(context):  # noqa: F811
    from meeting_minutes.minutes_calls import call_with_retries
    settings,repo=context
    register(context)
    job=repo.claim()
    update(repo,BudgetUpdate(expected_revision=1,token_limit=100))
    class Provider:
        def generate(self,payload,schema,reserve_call):
            reserve_call()
            return {'summary':'완성된 결과'}, {'usage':{'input_tokens':40,'output_tokens':70000},'completed':True}
    result=call_with_retries(repo,settings,job,Provider(),{},{},lambda v:v,'fingerprint')
    assert result['summary'] == '완성된 결과'
    assert get_status(repo)['used_tokens'] == 70040
    with pytest.raises(AIFailure,match='AI_WEEKLY_BUDGET_EXHAUSTED'):
        call_with_retries(repo,settings,job,Provider(),{},{},lambda v:v,'fingerprint')
    with repo.engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM usage_records')).scalar_one() == 1


def test_budget_wait_preserves_source_until_resume_or_cancel(context):  # noqa: F811
    from meeting_minutes.retention import expire_meeting
    settings,repo=context
    register(context)
    job=repo.claim()
    repo.finish(job['id'],job['attempt_id'],'BLOCKED','AI_WEEKLY_BUDGET_EXHAUSTED')
    expire_meeting(repo,settings,job['meeting_id'],10**10)
    assert repo.meeting(job['meeting_id'])['media_expired_at'] is None
    repo.cancel(job['id'])
    expire_meeting(repo,settings,job['meeting_id'],10**10)
    assert repo.meeting(job['meeting_id'])['media_expired_at'] is not None



def test_cross_provider_budgets_are_independent(context):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from meeting_minutes.ai_runtime import GenerationRuntime
    from meeting_minutes.ai_snapshot import configuration
    from meeting_minutes.minutes_calls import call_with_retries
    settings,repo=context
    register(context)
    with repo.write() as c:
        c.execute(text("UPDATE jobs SET kind='summarize'"))
    first=repo.claim('summary')
    register(context, name='second.wav')
    second=repo.claim('analysis')
    config=configuration(settings,{},'gemini_api','gemini-3.5-flash')
    with repo.write() as c:
        c.execute(text('UPDATE jobs SET ai_config_json=:value WHERE id=:id'),{'id':second['id'],'value':config.model_dump_json()})
    second=repo.job(second['id'])
    update(repo,BudgetUpdate(expected_revision=1,token_limit=100))
    entered,release=Event(),Event()
    calls=[]
    class Provider:
        def __init__(self,first): self.first=first
        def generate(self,payload,schema,reserve_call):
            reserve_call()
            calls.append(self.first)
            if self.first:
                entered.set()
                assert release.wait(5)
            return {},{'usage':{'input_tokens':50,'output_tokens':100} if self.first else {'total_token_count':90}}
    def run(job,provider,runtime=None):
        try:
            return call_with_retries(repo,settings,job,provider,{},{},lambda v:v,'hash',runtime=runtime)
        except AIFailure as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(run,first,Provider(True))
        assert entered.wait(5)
        b=pool.submit(run,second,Provider(False),GenerationRuntime(settings,config))
        release.set()
        assert a.result(timeout=10) == {}
        assert b.result(timeout=10) == {}
    assert sorted(calls) == [False,True] and get_status(repo)['used_tokens'] == 150
    assert get_status(repo,'gemini_api')['used_tokens'] == 90
    # Ledger has no job FK and remains when deletable per-meeting usage is removed.
    with repo.write() as c:
        c.execute(text('DELETE FROM usage_records'))
    assert get_status(repo)['used_tokens'] == 150


def test_migration_preserves_retained_usage_and_is_idempotent(tmp_path):
    import json
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from meeting_minutes.settings import Settings
    from meeting_minutes.storage import make_engine,migrate
    from meeting_minutes.repository import Repository
    settings=Settings(data_dir=tmp_path/'data',config_dir=tmp_path/'config',cache_dir=tmp_path/'cache',worker_enabled=False,codex_home=tmp_path/'codex')
    settings.prepare()
    engine=make_engine(settings.database_path)
    cfg=Config()
    cfg.set_main_option('script_location',str(Path(__file__).resolve().parents[1]/'src/meeting_minutes/migrations'))
    with engine.begin() as c:
        cfg.attributes['connection']=c
        command.upgrade(cfg,'0012')
    repo=Repository(engine)
    register((settings,repo))
    job=repo.jobs()[0]
    with repo.write() as c:
        c.execute(text("INSERT INTO usage_records VALUES ('historical',:job,:attempt,'SUMMARIZE',:metrics,:now)"),
            {'job':job['id'],'attempt':job['attempt_id'],'metrics':json.dumps({'call_reserved':True,'usage':{'input_tokens':80,'output_tokens':20}}),'now':week()[0]+1})
    migrate(engine)
    migrate(engine)
    assert get_status(repo)['used_tokens'] == 100
    engine.dispose()



def test_provider_limits_revisions_and_resume_are_independent(context):  # noqa: F811
    import json
    from meeting_minutes.ai_snapshot import configuration
    settings,repo=context
    assert {p:get_status(repo,p)['token_limit'] for p in ('codex_cli','claude_cli')} == {
        'codex_cli':20000000,'claude_cli':20000000}
    update(repo,RequestBudgetUpdate(expected_revision=1,request_limit=1,requests_per_minute=2),'gemini_api')
    update(repo,BudgetUpdate(expected_revision=1,token_limit=100),'claude_cli')
    assert get_status(repo,'codex_cli')['revision'] == 1
    register(context)
    config=configuration(settings,{},'gemini_api','gemini-3.5-flash')
    with repo.write() as c:
        c.execute(text('UPDATE jobs SET ai_config_json=:raw'),{'raw':config.model_dump_json()})
        reserve(c,'gemini-spent','gemini_api',11)
        settle(c,'gemini-spent',{'total_token_count':11})
    job=repo.claim('analysis')
    repo.finish(job['id'],job['attempt_id'],'BLOCKED','AI_WEEKLY_BUDGET_EXHAUSTED')
    update(repo,BudgetUpdate(expected_revision=1,token_limit=30000000),'codex_cli')
    assert repo.claim('analysis') is None
    update(repo,RequestBudgetUpdate(expected_revision=2,request_limit=2,requests_per_minute=2),'gemini_api')
    resumed=repo.claim('analysis')
    assert resumed['id'] == job['id']
    assert json.loads(resumed['ai_config_json'])['provider'] == 'gemini_api'

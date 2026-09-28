from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import text
from meeting_minutes import gemini_budget
from meeting_minutes.ai_budget import reserve, settle, get_status, update, BudgetUpdate
from meeting_minutes.ai_common import AIFailure
from meeting_minutes.repository import Conflict
from test_queue_media import context, register  # noqa: F401


def test_daily_minute_reservations_restart_and_refill(context, monkeypatch):  # noqa: F811
    _, repo = context
    now = datetime(2026, 11, 1, 0, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    monkeypatch.setattr(gemini_budget.time, 'time', lambda: now)
    assert get_status(repo,'gemini_api')['refills_at'] - now == 25 * 3600
    for i in range(2):
        with repo.write() as c:
            reserve(c,str(i),'gemini_api',3000000)
            settle(c,str(i),None)  # Unknown and failed attempts are not refunded.
    with pytest.raises(AIFailure,match='AI_REQUEST_RATE_WAIT'), repo.write() as c:
        reserve(c,'denied','gemini_api',1)
    assert get_status(repo,'gemini_api')['used_requests'] == 2
    now += 60
    for i in range(2,10):
        with repo.write() as c:
            reserve(c,str(i),'gemini_api',1)
        now += 60
    with pytest.raises(AIFailure,match='AI_DAILY_REQUEST_BUDGET_EXHAUSTED'), repo.write() as c:
        reserve(c,'eleventh','gemini_api',1)
    assert get_status(repo,'gemini_api')['used_requests'] == 10
    now = get_status(repo,'gemini_api')['refills_at']
    assert get_status(repo,'gemini_api')['used_requests'] == 0
    with repo.write() as c:
        reserve(c,'tomorrow','gemini_api',1)
    assert get_status(repo,'codex_cli')['used_tokens'] == 0


def test_daily_policy_strict_units_conflict_and_automatic_resume(context, monkeypatch):  # noqa: F811
    from meeting_minutes.ai_snapshot import configuration
    settings, repo = context
    now = 1800000000.0
    monkeypatch.setattr(gemini_budget.time, 'time', lambda: now)
    register(context)
    with repo.write() as c:
        c.execute(text('UPDATE jobs SET ai_config_json=:config'), {'config':configuration(settings,{},'gemini_api','gemini-3.8-flash').model_dump_json()})
        reserve(c,'one','gemini_api',1)
        reserve(c,'two','gemini_api',1)
    job = repo.claim()
    repo.finish(job['id'],job['attempt_id'],'BLOCKED','AI_REQUEST_RATE_WAIT')
    assert repo.claim() is None
    now += 60
    resumed = repo.claim()
    assert resumed['id'] == job['id'] and resumed['ai_config_json'] == job['ai_config_json']
    with pytest.raises(Conflict,match='AI_BUDGET_UNIT_MISMATCH'):
        update(repo,BudgetUpdate(expected_revision=1,token_limit=100),'gemini_api')
    request = gemini_budget.RequestBudgetUpdate(expected_revision=1,request_limit=20,requests_per_minute=5)
    assert update(repo,request,'gemini_api')['request_limit'] == 20
    with pytest.raises(Conflict,match='AI_BUDGET_REVISION_CONFLICT'):
        update(repo,request,'gemini_api')


def test_daily_api_owner_csrf_and_units(context):  # noqa: F811
    from fastapi.testclient import TestClient
    from meeting_minutes.api import create_app
    from test_ai_policy import login
    settings,_ = context
    with TestClient(create_app(settings),base_url=settings.origin) as client:
        route = '/api/settings/ai/budget/gemini_api'
        assert client.get(route).status_code == 401
        headers = login(client,settings)
        body = {'expected_revision':1,'request_limit':12,'requests_per_minute':2}
        assert client.patch(route,json=body).status_code == 403
        assert client.patch(route,json=dict(body,request_limit=True),headers=headers).status_code == 422
        assert client.patch(route,json=body,headers=headers).json()['request_limit'] == 12
        assert client.patch(route,json=body,headers=headers).status_code == 409


def test_migration_conservatively_preserves_old_reservations(tmp_path, monkeypatch):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from meeting_minutes.storage import make_engine, migrate
    from meeting_minutes.ai_budget import week
    from meeting_minutes.repository import Repository
    # Monday KST is still Sunday Pacific; today's earlier calls can be in the prior KST week.
    now = datetime(2026,9,28,1,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
    monkeypatch.setattr(gemini_budget.time,'time',lambda:now)
    engine = make_engine(tmp_path / 'old.sqlite3')
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/meeting_minutes/migrations'))
    with engine.begin() as c:
        config.attributes['connection'] = c
        command.upgrade(config,'0015')
        c.execute(text("INSERT INTO ai_budget_ledger VALUES ('old-unknown',:week,123,'UNKNOWN','gemini_api')"), {'week':week(now-7200)[0]})
    migrate(engine)
    migrate(engine)
    repo = Repository(engine)
    current = get_status(repo,'gemini_api')
    assert current['used_requests'] == 1 and current['reserved_tokens'] == 0
    with engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM gemini_request_ledger')).scalar_one() == 1
        assert c.execute(text('SELECT SUM(tokens) FROM ai_budget_ledger')).scalar_one() == 123


def test_daily_limit_raise_and_midnight_resume_preserve_fifo(context, monkeypatch):  # noqa: F811
    from meeting_minutes.ai_snapshot import configuration
    settings,repo = context
    now = 1800000000.0
    monkeypatch.setattr(gemini_budget.time,'time',lambda:now)
    register(context)
    update(repo,gemini_budget.RequestBudgetUpdate(expected_revision=1,request_limit=1,requests_per_minute=5),'gemini_api')
    with repo.write() as c:
        c.execute(text('UPDATE jobs SET ai_config_json=:config'), {'config':configuration(settings,{},'gemini_api','gemini-3.8-flash').model_dump_json()})
        reserve(c,'spent','gemini_api',1)
    job = repo.claim()
    repo.finish(job['id'],job['attempt_id'],'BLOCKED','AI_DAILY_REQUEST_BUDGET_EXHAUSTED')
    assert repo.claim() is None
    update(repo,gemini_budget.RequestBudgetUpdate(expected_revision=2,request_limit=2,requests_per_minute=5),'gemini_api')
    resumed = repo.claim()
    assert resumed['id'] == job['id']
    with repo.write() as c:
        reserve(c,'spent-again','gemini_api',1)
    repo.finish(resumed['id'],resumed['attempt_id'],'BLOCKED','AI_DAILY_REQUEST_BUDGET_EXHAUSTED')
    assert repo.claim() is None
    now = get_status(repo,'gemini_api')['refills_at']
    assert repo.claim()['id'] == job['id']

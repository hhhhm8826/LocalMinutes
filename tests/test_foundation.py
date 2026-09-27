import importlib.util
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from meeting_minutes.api import create_app
from meeting_minutes.contracts import MeetingCreate, Minutes, Segment
from meeting_minutes.settings import Settings
from meeting_minutes.storage import make_engine, migrate


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", config_dir=tmp_path / "config",
                    cache_dir=tmp_path / "cache", worker_enabled=False)


def test_schema_rejects_invalid_meeting_and_time():
    for values in ({"speakers": 13}, {"speakers": 0}, {"timezone": "not-a-zone"},
                   {"occurred_at": "2026-01-01T10:00:00"}):
        with pytest.raises(ValidationError):
            MeetingCreate(title="회의", **values)
    with pytest.raises(ValidationError):
        Segment(id="s1", start_ms=20, end_ms=10, text="말")
    value = Minutes(meeting_id="m1", transcript_version="v1", revision=1, summary="요약",
                    topics=[], decisions=[], action_items=[], open_questions=[], review_notes=[])
    assert Minutes.model_validate_json(value.model_dump_json()) == value


def test_migration_is_repeatable_and_single_active_job_is_enforced(settings):
    settings.prepare()
    engine = make_engine(settings.database_path)
    migrate(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO meetings(id,title,settings_json,created_at,updated_at) VALUES ('m','회의','{}',0,0)"))
    migrate(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM meetings")).scalar_one() == "회의"
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005"
        columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(jobs)')}
        assert 'process_identity_json' in columns
    insert = text("""INSERT INTO jobs(id,meeting_id,kind,state,stage,attempt_id,idempotency_key,request_hash,created_at,updated_at)
        VALUES (:id,'m','transcribe','RUNNING','VALIDATE',:id,:id,'hash',0,0)""")
    with engine.begin() as connection:
        connection.execute(insert, {"id": "first"})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert, {"id": "second"})
    engine.dispose()


def test_owner_auth_origin_host_csrf_and_restart(settings):
    app = create_app(settings)
    with TestClient(app, base_url=settings.origin) as client:
        assert "torch" not in sys.modules
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/diagnostics").status_code == 401
        assert client.get("/api/health", headers={"host": "attacker.example"}).status_code == 400
        key = settings.owner_key_path.read_text()
        assert settings.owner_key_path.stat().st_mode & 0o777 == 0o600
        assert client.post("/api/auth/login", json={"key": key}).status_code == 403
        assert client.post("/api/auth/login", json={"key": key}, headers={"origin": "https://evil.example"}).status_code == 403
        headers = {"origin": settings.origin}
        assert client.post("/api/auth/login", json={"key": "wrong"}, headers=headers).status_code == 401
        result = client.post("/api/auth/login", json={"key": key}, headers=headers)
        assert result.status_code == 200
        assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=strict" in result.headers["set-cookie"]
        csrf = result.json()["csrf_token"]
        cookies = dict(client.cookies)
        assert client.get("/api/diagnostics").json()["device"] == "cpu"
        assert key not in client.get("/api/diagnostics").text
        assert client.post("/api/auth/logout", headers=headers).status_code == 403
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        client.cookies.update(cookies)
        assert client.get("/api/auth/session").json()["csrf_token"] == csrf
        assert client.post("/api/auth/logout", headers={"origin": settings.origin, "x-csrf-token": csrf}).status_code == 200
        assert client.get("/api/diagnostics").status_code == 401


def test_non_loopback_configuration_rejected():
    for origin in ("http://0.0.0.0:8765", "https://example.com", "http://localhost:8765/path"):
        with pytest.raises(ValidationError):
            Settings(origin=origin)


def test_review_dispatch_gates():
    spec = importlib.util.spec_from_file_location("dispatch", Path(__file__).parents[1] / "scripts/verify_dispatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    packet = {"gate": "R1", "head_commit": "a" * 40, "allow_full_suite": False,
              "allow_model_run": False, "allow_live_codex": False}
    module.validate_scope(packet)
    packet["allow_full_suite"] = True
    with pytest.raises(ValueError):
        module.validate_scope(packet)
    packet["gate"] = "R3"
    module.validate_scope(packet)
    packet["allow_live_codex"] = True
    with pytest.raises(ValueError):
        module.validate_scope(packet)
    packet["execution_budget"] = {"max_codex_calls": 1}
    module.validate_scope(packet)


def test_review_dispatch_duplicate_reserved_once(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("dispatch", Path(__file__).parents[1] / "scripts/verify_dispatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master, verifier = '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'
    registry = tmp_path / 'session-registry.json'
    registry.write_text(json.dumps({'master': {'thread_id': master}, 'verifier': {'thread_id': verifier},
                                   'shared_workflow_root': str(tmp_path), 'queue_cli': 'fake-cli'}))
    folder = tmp_path / 'reviews' / 'R1-001'
    folder.mkdir(parents=True)
    packet = {'request_id': 'R1-001', 'reply_to_thread_id': master, 'gate': 'R1',
              'head_commit': 'a' * 40, 'allow_full_suite': False, 'allow_model_run': False, 'allow_live_codex': False}
    (folder / 'request.json').write_text(json.dumps(packet))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return module.subprocess.CompletedProcess(argv, 0, stdout='queued', stderr='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['dispatch', '--registry', str(registry), '--request-id', 'R1-001'])
    module.main()
    module.main()
    assert len(calls) == 1
    assert calls[0][3] == verifier
    assert 'VERIFY_REQUEST id=R1-001; commit=' in calls[0][-1]

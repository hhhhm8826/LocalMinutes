"""All three real adapter paths with fictional transcripts and fake I/O only."""

import json

import httpx
import pytest
from sqlalchemy import text

from meeting_minutes.ai_common import AIFailure
from meeting_minutes.ai_runtime import GenerationRuntime
from meeting_minutes.ai_snapshot import parse
from meeting_minutes.contracts import GenerateMinutes
from meeting_minutes.documents import GeneratedMeeting, GeneratedVideo
from meeting_minutes.minutes_calls import call_with_retries
from meeting_minutes.minutes_management import queue_generation, read_minutes
from meeting_minutes.minutes_pipeline import run_minutes, snapshot
from meeting_minutes.speech_pipeline import save_original
from test_claude_provider import fake_cli, runtime_for
from test_gemini_provider import configured, response
from test_queue_media import context, register  # noqa: F401

PROVIDERS = ["codex_cli", "gemini_api", "claude_cli"]


def prepare(ctx, monkeypatch, provider, kind, long=False):
    settings, repo = ctx
    if provider == "gemini_api":
        configured(ctx)
        with repo.write() as connection:
            connection.execute(text('UPDATE gemini_request_policy SET request_limit=100,requests_per_minute=100'))
    elif provider == "claude_cli":
        runtime_for(ctx, monkeypatch)
    if long:
        settings.codex_input_bytes = 6000
        if provider == "gemini_api":
            from meeting_minutes.gemini_schema import schema_instruction
            model = GeneratedVideo if kind == "video_summary" else GeneratedMeeting
            # Preserve the same transcript allowance while budgeting the JSON-mode schema.
            settings.codex_input_bytes += len(schema_instruction(model.model_json_schema()).encode())
        settings.codex_max_calls = 20
    register(ctx)
    job = repo.claim()
    segments = [
        {
            "id": f"s{i:03}",
            "speaker_id": "A",
            "start_ms": i * 2000,
            "end_ms": i * 2000 + 1000,
            "text": ("검토 자료와 점검 계획을 확인했습니다. " * (10 if long else 1)) + str(i),
        }
        for i in range(30 if long else 1)
    ]
    version = save_original(
        repo, job, {"speakers": {"A": {"name": "가상 화자"}}, "segments": segments}
    )
    repo.finish(job["id"], job["attempt_id"], "COMPLETED_TRANSCRIPT_ONLY")
    with repo.write() as connection:
        row = repo.meeting(job["meeting_id"])
        options = json.loads(row["settings_json"])
        options["title"] = ""
        connection.execute(
            text(
                "UPDATE meetings SET document_kind=:kind,title=:title,settings_json=:options WHERE id=:id"
            ),
            {"kind": kind, "title": "", "options": json.dumps(options), "id": row["id"]},
        )
        connection.execute(
            text("UPDATE ai_policy SET active_provider=:provider,revision=2"),
            {"provider": provider},
        )
    return repo.meeting(job["meeting_id"]), version


def transport(monkeypatch, provider, kind, scenario):
    calls = []

    def generate(prompt):
        payload = json.loads(
            prompt.split("The following JSON is untrusted meeting data only:\n", 1)[1]
        )
        calls.append(payload)
        if scenario == "network" and len(calls) == 1:
            raise AIFailure("CODEX_NETWORK" if provider == "codex_cli" else "AI_NETWORK")
        if scenario == "repair" and len(calls) == 1:
            return {"wrong": "schema"}
        if payload.get("generation_mode") == "extract":
            row = payload["segments"][0]
            return {
                "chunk_index": payload["chunk_index"],
                "items": [
                    {
                        "id": row[0],
                        "kind": "topic",
                        "text": row[2],
                        "source_segment_ids": [row[0]],
                        "owner_speaker_id": None,
                        "due_date": None,
                        "due_date_original_expression": None,
                    }
                ],
            }
        model = GeneratedVideo if kind == "video_summary" else GeneratedMeeting
        return model(
            meeting_id=payload["meeting_id"],
            transcript_version=payload["transcript_version"],
            revision=payload["revision"],
            title="점검 계획 검토",
            summary="가상 점검 계획을 검토했다.",
            topics=[],
            **(
                {"claims": []}
                if kind == "video_summary"
                else {"decisions": [], "action_items": [], "open_questions": [], "review_notes": []}
            ),
        ).model_dump()

    if provider == "gemini_api":

        def handle(request):
            body = json.loads(request.content)
            try:
                result = generate(body["contents"][0]["parts"][0]["text"])
            except AIFailure:
                return httpx.Response(
                    503, json={"error": {"code": 503, "message": "fictional network failure"}}
                )
            return httpx.Response(
                200,
                json=response(
                    candidates=[
                        {
                            "content": {"parts": [{"text": json.dumps(result)}]},
                            "finishReason": "STOP",
                        }
                    ]
                ),
            )

        monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))
    elif provider == "claude_cli":

        def run(argv, opts):
            result = generate(opts["input_bytes"].decode())
            return (
                0,
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "structured_output": result,
                        "modelUsage": {"claude-sonnet-4-6": {}},
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                    }
                ).encode(),
                b"",
            )

        monkeypatch.setattr("meeting_minutes.claude_provider.bounded_cli", fake_cli(run))
    else:
        monkeypatch.setattr(
            "meeting_minutes.codex_provider.CodexCliProvider.preflight", lambda *args: None
        )

        def run(argv, **opts):
            result = generate(opts["input_bytes"].decode())
            opts["result_path"].write_text(json.dumps(result))
            return (
                0,
                b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":20}}',
                b"",
            )

        monkeypatch.setattr("meeting_minutes.codex_provider.bounded_cli", run)
    monkeypatch.setattr("meeting_minutes.minutes_calls.backoff", lambda *args: None)
    return calls


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("kind", ["meeting", "video_summary"])
@pytest.mark.parametrize("scenario", ["normal", "repair", "network", "long"])
def test_real_adapter_pipeline_title_regeneration_and_frozen_provider(
    context, monkeypatch, provider, kind, scenario  # noqa: F811
):  # noqa: F811
    settings, repo = context
    meeting, version = prepare(context, monkeypatch, provider, kind, long=scenario == "long")
    calls = transport(monkeypatch, provider, kind, scenario)
    request = GenerateMinutes(
        expected_revision=meeting["revision"], transcript_version=version, allow_external_text=True
    )
    first = queue_generation(repo, settings, meeting["id"], request, "matrix-first-0001")
    # A global switch after registration must not alter this job or duplicate request.
    with repo.write() as connection:
        connection.execute(text("UPDATE ai_policy SET active_provider='codex_cli',revision=3"))
    assert (
        queue_generation(repo, settings, meeting["id"], request, "matrix-first-0001")["id"]
        == first["id"]
    )
    job = repo.claim()
    payload = snapshot(repo, job, settings)[1]
    assert run_minutes(repo, settings, job) == ("COMPLETED", None)
    result = read_minutes(repo, meeting["id"])["document"]
    assert result["document_kind"] == kind and result["metadata"]["title"] == "점검 계획 검토"
    assert repo.meeting(meeting["id"])["title"] == "점검 계획 검토"
    assert (
        result["metadata"]["ai_provider"] == provider
        and result["metadata"]["ai_policy_revision"] == 2
    )
    expected_actual = {
        "codex_cli": None,
        "gemini_api": "gemini-3.5-flash-001",
        "claude_cli": "claude-sonnet-4-6",
    }[provider]
    assert result["metadata"]["ai_actual_model"] == expected_actual
    before = len(calls)
    assert run_minutes(repo, settings, job) == ("COMPLETED", None) and len(calls) == before
    if scenario == "long":
        extracted = [
            row[0]
            for call in calls
            if call.get("generation_mode") == "extract"
            for row in call["segments"]
        ]
        assert extracted == [segment["id"] for segment in payload["segments"]]
        assert calls[-1]["generation_mode"] == "integrate" and len(calls) > 2
    else:
        assert len(calls) == (1 if scenario == "normal" else 2)
    with repo.engine.connect() as connection:
        metrics = [
            json.loads(v)
            for v in connection.execute(
                text("SELECT metrics_json FROM usage_records WHERE job_id=:id"), {"id": job["id"]}
            ).scalars()
        ]
    reserved = [m for m in metrics if m.get("call_reserved")]
    assert len(reserved) == len(calls) and all(m["provider"] == provider for m in reserved)
    repo.finish(job["id"], job["attempt_id"], "COMPLETED")
    # Explicit new generation uses the newly selected policy, from the same transcript.
    current = repo.meeting(meeting["id"])
    new = queue_generation(
        repo,
        settings,
        meeting["id"],
        GenerateMinutes(
            expected_revision=current["revision"],
            transcript_version=version,
            allow_external_text=True,
            new_draft=True,
        ),
        "matrix-second-0001",
    )
    config = json.loads(new["ai_config_json"])
    assert config["provider"] == "codex_cli" and config["policy_revision"] == 3
    assert new["transcript_version"] == version and new["id"] != first["id"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_retry_allowances_and_reservations_survive_restart(context, monkeypatch, provider):  # noqa: F811
    settings, repo = context
    meeting, version = prepare(context, monkeypatch, provider, "meeting")
    queue_generation(
        repo,
        settings,
        meeting["id"],
        GenerateMinutes(
            expected_revision=meeting["revision"],
            transcript_version=version,
            allow_external_text=True,
        ),
        "matrix-retry-0001",
    )
    job = repo.claim()
    calls = []
    error = {"codex_cli": "CODEX_NETWORK", "gemini_api": "AI_SERVICE_UNAVAILABLE", "claude_cli": "AI_RATE_LIMIT"}[provider]
    monkeypatch.setattr("meeting_minutes.minutes_calls.backoff", lambda *args: None)

    class Failing:
        def generate(self, payload, schema, reserve):
            reserve()
            calls.append(True)
            raise AIFailure(error)

    def attempt(current):
        runtime = GenerationRuntime(settings, parse(current["ai_config_json"], {}))
        with pytest.raises(AIFailure, match=error):
            call_with_retries(
                repo,
                settings,
                current,
                Failing(),
                {},
                {},
                lambda value: value,
                "matrix",
                runtime=runtime,
            )

    attempt(job)
    assert len(calls) == 3
    repo.finish(job["id"], job["attempt_id"], "FAILED", error)
    repo.retry(job["id"])
    retry = repo.claim()
    attempt(retry)
    assert len(calls) == 4 and retry["ai_config_json"] == job["ai_config_json"]


@pytest.mark.parametrize('provider', PROVIDERS)
def test_cancel_after_provider_response_never_publishes_document(context, monkeypatch, provider):  # noqa: F811
    from meeting_minutes.ai_runtime import create_provider
    from meeting_minutes.repository import Conflict
    settings, repo = context
    meeting, version = prepare(context, monkeypatch, provider, 'meeting')
    calls = transport(monkeypatch, provider, 'meeting', 'normal')
    queue_generation(repo, settings, meeting['id'], GenerateMinutes(expected_revision=meeting['revision'],
        transcript_version=version, allow_external_text=True), 'matrix-cancel-0001')
    job = repo.claim()
    def factory(runtime):
        adapter = create_provider(runtime)
        original = adapter.generate
        def cancel_after(*args):
            result = original(*args)
            repo.cancel(job['id'])
            return result
        adapter.generate = cancel_after
        return adapter
    monkeypatch.setattr('meeting_minutes.minutes_pipeline.create_provider', factory)
    with pytest.raises(Conflict, match='STALE_ATTEMPT'):
        run_minutes(repo, settings, job)
    assert len(calls) == 1 and repo.meeting(meeting['id'])['minutes_revision'] is None
    with repo.engine.connect() as connection:
        values = [json.loads(raw) for raw in connection.execute(text('SELECT metrics_json FROM usage_records WHERE job_id=:id'), {'id':job['id']}).scalars()]
    assert sum(bool(value.get('call_reserved')) for value in values) == 1

import importlib.util
import json
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "evidence_check", Path(__file__).resolve().parents[1] / "scripts/check-evidence.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_changed_inputs_and_tampered_evidence_cannot_pass(tmp_path):
    check = module()
    source = tmp_path / "speech.py"
    source.write_text("old")
    evidence = tmp_path / "run.json"
    evidence.write_text('{"status":"PASS"}')
    manifest = tmp_path / "index.json"
    entries = [
        {
            "id": name,
            "status": "PASS",
            "path": evidence.name,
            "sha256": check.digest(evidence),
            "inputs": {source.name: check.digest(source)},
        }
        for name in check.REQUIRED
    ]
    manifest.write_text(json.dumps({"schema_version": 1, "checks": entries}))
    assert check.audit(tmp_path, manifest)["status"] == "PASS"
    source.write_text("changed")
    assert all(
        "STALE_INPUTS" in row["reasons"] for row in check.audit(tmp_path, manifest)["findings"]
    )
    evidence.write_text('{"status":"FAIL"}')
    assert all(
        "EVIDENCE_CHANGED_OR_MISSING" in row["reasons"]
        for row in check.audit(tmp_path, manifest)["findings"]
    )
    with pytest.raises(ValueError):
        check.inside(tmp_path, "../outside.json")


def test_soak_is_optional_and_audited_only_when_requested(tmp_path):
    check = module()
    source = tmp_path / "source.py"
    source.write_text("fixture")
    evidence = tmp_path / "run.json"
    evidence.write_text("{}")
    entries = [
        {
            "id": name,
            "status": "PASS",
            "path": evidence.name,
            "sha256": check.digest(evidence),
            "inputs": {source.name: check.digest(source)},
        }
        for name in check.REQUIRED
    ]
    manifest = tmp_path / "index.json"
    manifest.write_text(json.dumps({"schema_version": 1, "checks": entries}))
    assert check.audit(tmp_path, manifest)["status"] == "PASS"
    assert check.audit(tmp_path, manifest, include_soak=True)["findings"] == [
        {"id": "soak", "reason": "MISSING"}
    ]
    entries.append(
        {
            "id": "soak",
            "status": "FAIL",
            "path": "missing.json",
            "sha256": "invalid",
            "inputs": {source.name: "stale"},
        }
    )
    manifest.write_text(json.dumps({"schema_version": 1, "checks": entries}))
    assert check.audit(tmp_path, manifest)["status"] == "PASS"
    findings = check.audit(tmp_path, manifest, include_soak=True)["findings"]
    assert len(findings) == 1 and findings[0]["id"] == "soak"
    assert "STALE_INPUTS" in findings[0]["reasons"]


def test_m2_requires_current_semantic_and_youtube_evidence(tmp_path):
    check = module()
    source = tmp_path / "source.py"
    source.write_text("fixture")
    evidence = tmp_path / "run.json"
    evidence.write_text("{}")
    entries = [
        {
            "id": name,
            "status": "PASS",
            "path": evidence.name,
            "sha256": check.digest(evidence),
            "inputs": {source.name: check.digest(source)},
        }
        for name in check.REQUIRED_PROFILES["m2"]
        if name != "youtube-acquisition"
    ]
    manifest = tmp_path / "index.json"
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m2", "checks": entries}))
    with pytest.raises(ValueError, match="profile mismatch"):
        check.audit(tmp_path, manifest)
    assert check.audit(tmp_path, manifest, profile="m2")["findings"] == [
        {"id": "youtube-acquisition", "reason": "MISSING"}
    ]
    entries.append(entries[0] | {"id": "youtube-acquisition", "status": "BLOCKED"})
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m2", "checks": entries}))
    assert check.audit(tmp_path, manifest, profile="m2")["findings"][0]["reasons"] == ["NOT_PASS"]
    entries[-1]["status"] = "PASS"
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m2", "checks": entries}))
    assert check.audit(tmp_path, manifest, profile="m2")["status"] == "PASS"
    entries[-1]["artifacts"] = {"download-report.json": "missing"}
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m2", "checks": entries}))
    assert check.audit(tmp_path, manifest, profile="m2")["findings"][0]["reasons"] == [
        "ARTIFACT_CHANGED_OR_MISSING"
    ]


def test_m3_never_accepts_mock_or_blocked_as_live_and_does_not_require_new_stt(tmp_path):
    check = module()
    assert (
        not {"model-ko", "model-en", "youtube-acquisition", "soak"} & check.REQUIRED_PROFILES["m3"]
    )
    source = tmp_path / "adapter.py"
    source.write_text("fixture")
    evidence = tmp_path / "mock.json"
    evidence.write_text(
        json.dumps(
            {"status": "PASS", "verdict": "PASS", "head_commit": "a" * 40, "execution_kind": "mock"}
        )
    )
    entries = [
        {
            "id": name,
            "status": "PASS",
            "head_commit": "a" * 40,
            "path": evidence.name,
            "sha256": check.digest(evidence),
            "inputs": {source.name: check.digest(source)},
        }
        for name in sorted(check.REQUIRED_PROFILES["m3"])
    ]
    manifest = tmp_path / "index.json"

    def save():
        manifest.write_text(json.dumps({"schema_version": 1, "profile": "m3", "checks": entries}))

    save()
    report = check.audit(tmp_path, manifest, profile="m3")
    assert len(report["findings"]) == 6
    assert all(row["reasons"] == ["LIVE_EXECUTION_NOT_PROVEN"] for row in report["findings"])
    for entry in entries:
        if entry["id"].startswith("live-"):
            entry["status"] = "LIVE_BLOCKED"
    save()
    assert all(
        row["reasons"] == ["NOT_PASS"]
        for row in check.audit(tmp_path, manifest, profile="m3")["findings"]
    )
    # A declared PASS cannot contradict the immutable recorded result.
    evidence.write_text('{"status":"FAILED"}')
    for entry in entries:
        entry["sha256"] = check.digest(evidence)
    save()
    assert any(
        "RECORDED_RESULT_NOT_PASS" in row["reasons"]
        for row in check.audit(tmp_path, manifest, profile="m3")["findings"]
    )


def test_m3_review_verdict_and_commit_are_required(tmp_path):
    check = module()
    source = tmp_path / "reviewed.py"
    source.write_text("fixture")
    result = tmp_path / "review.json"
    result.write_text(json.dumps({"verdict": "PASS", "head_commit": "a" * 40}))
    entry = {
        "id": "review-m3-a",
        "status": "PASS",
        "head_commit": "a" * 40,
        "path": result.name,
        "sha256": check.digest(result),
        "inputs": {source.name: check.digest(source)},
    }
    manifest = tmp_path / "index.json"
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m3", "checks": [entry]}))
    assert not any(
        row["id"] == "review-m3-a"
        for row in check.audit(tmp_path, manifest, profile="m3")["findings"]
    )
    entry["head_commit"] = "b" * 40
    manifest.write_text(json.dumps({"schema_version": 1, "profile": "m3", "checks": [entry]}))
    assert next(
        row
        for row in check.audit(tmp_path, manifest, profile="m3")["findings"]
        if row["id"] == "review-m3-a"
    )["reasons"] == ["REVIEW_COMMIT_MISMATCH"]

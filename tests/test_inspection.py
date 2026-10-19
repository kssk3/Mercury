"""Historical status contracts; fixtures mirror stored producer field shapes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from agent_harness.inspection import summarize_execution


def write_history(directory: Path, status: str = "pass") -> dict[str, Any]:
    directory.mkdir(exist_ok=True)
    identity = {
        "attempt_id": "attempt-one",
        "repository_root": "/recorded/repository",
        "repository_id": "repo-one",
        "contract_sha256": "a" * 64,
        "context_sha256": "b" * 64,
        "before_head": "c" * 40,
    }
    output = {
        "identifier": "d" * 64,
        "byte_count": 12,
        "truncated": False,
        "captured_bytes": 12,
        "capture_truncated": False,
    }
    verification = {
        **identity,
        "event": "attempt_verification",
        "schema_version": 1,
        "target_revision": "c" * 40,
        "technical_kind": "pass",
        "overall_kind": "pass",
        "commands": [{"command_sha256": "e" * 64, "exit_code": 0, "timed_out": False}],
        "baseline_commands": [
            {"command_sha256": "e" * 64, "exit_code": 1, "timed_out": False}
        ],
        "reason_command_sha256": [],
        "protected_inputs": [],
        "output_identifiers": {
            "stdout": "d" * 64,
            "stderr": "d" * 64,
            "final_message": "d" * 64,
        },
    }
    process = {
        "process_status": "successful_exit",
        "exit_code": 0,
        "stdout": {"retained_bytes": 12, "total_bytes": 12, "truncated": False},
        "stderr": {"retained_bytes": 12, "total_bytes": 12, "truncated": False},
        "final_message": {"retained_bytes": 12, "total_bytes": 12, "truncated": False},
        "final_output_status": "available",
        "duration_seconds": 0.1,
    }
    observation = {
        "repository_root": identity["repository_root"],
        "repository_id": "repo-one",
        "head": "c" * 40,
        "index_tree": "f" * 40,
        "status": [],
        "files": [],
    }
    scope = {
        "proceed": True,
        "delta": {
            "added": [],
            "modified": [],
            "deleted": [],
            "status_changed": False,
            "head_changed": False,
            "index_changed": False,
        },
        "out_of_scope_paths": [],
        "protected_paths": [],
        "unexplained_status_change": False,
    }
    events: list[dict[str, Any]] = [
        {"event": "loop_start", "repository_id": "repo-one"},
        {
            **identity,
            "event": "turn_intent",
            "before": observation,
            "sandbox": "workspace-write",
            "budgets": {
                "timeout_seconds": 2,
                "input_bytes": 100,
                "stdout_bytes": 100,
                "stderr_bytes": 100,
                "final_bytes": 100,
            },
        },
        {
            "event": "turn_observation",
            "attempt_id": "attempt-one",
            "after": observation,
            "process": process,
            "delta": scope["delta"],
        },
        {
            "event": "scope_decision",
            "attempt_id": "attempt-one",
            "repository_id": "repo-one",
            **scope,
        },
        {
            "event": "native_output_stored",
            "attempt_id": "attempt-one",
            "repository_id": "repo-one",
            "stdout": output,
            "stderr": output,
            "final_message": output,
        },
        verification,
    ]
    active = {
        **identity,
        "after_head": "c" * 40,
        "observation": {"status": "observed", "process": process},
        "scope": scope,
        "outputs": {"stdout": output, "stderr": output, "final_message": output},
        "verification": verification,
    }
    outcome = {
        "event": "loop_outcome",
        "status": status,
        "reason": "final_verification" if status == "pass" else "command_budget",
        "commands": 4,
        "elapsed_seconds": 0.5,
        "elapsed_boundary": "before_outcome_persistence",
        "attempt_ids": ["attempt-one"],
    }
    prefix = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    state = {
        "attempt_summary": {
            "schema_version": 1,
            "attempt_count": 1,
            "journal": {
                "path": str(directory / "events.jsonl"),
                "sha256": hashlib.sha256(prefix).hexdigest(),
            },
            "active_attempt": active,
        },
        "loop_summary": outcome,
        "private_transcript": "PRIVATE-MARKER",
    }
    (directory / "events.jsonl").write_bytes(
        prefix + json.dumps(outcome).encode() + b"\n"
    )
    (directory / "current.json").write_text(json.dumps(state))
    return state


def store_state(directory: Path, state: dict[str, Any]) -> None:
    (directory / "current.json").write_text(json.dumps(state))


def test_status_supported_producer_prefix_is_historical_and_does_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_history(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def forbid(*args: object, **kwargs: object) -> None:
        pytest.fail("status attempted execution")

    monkeypatch.setattr(subprocess, "Popen", forbid)
    summary = summarize_execution(tmp_path)
    assert summary["recorded_status"] == "pass"
    assert summary["recorded_reason"] == "final_verification"
    assert summary["attempt_ids"] == ["attempt-one"]
    assert summary["target_revision"] == "c" * 40
    assert summary["historical_only"] is True
    assert summary["current_workspace_verified"] is False
    assert summary["schema_version"] == 1
    assert summary["verification"] == {
        "technical_kind": "pass",
        "overall_kind": "pass",
        "command_count": 1,
    }
    assert cast(dict[str, Any], summary["evidence"])["outputs"]["stdout"] == "d" * 64
    assert "PRIVATE-MARKER" not in json.dumps(summary)
    assert "commands" not in summary
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("recorded", ["fail", "blocked", "stopped", "human", "unknown"])
def test_status_preserves_explicit_nonpass_outcomes(
    tmp_path: Path, recorded: str
) -> None:
    write_history(tmp_path, recorded)
    assert summarize_execution(tmp_path)["recorded_status"] == recorded


def test_status_accepts_current_full_journal_hash(tmp_path: Path) -> None:
    state = write_history(tmp_path)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(
        (tmp_path / "events.jsonl").read_bytes()
    ).hexdigest()
    store_state(tmp_path, state)
    assert summarize_execution(tmp_path)["recorded_status"] == "pass"


@pytest.mark.parametrize("status", ["pass", "fail", "blocked"])
def test_status_accepts_valid_run_binding_prefix(tmp_path: Path, status: str) -> None:
    state = write_history(tmp_path, status)
    journal = tmp_path / "events.jsonl"
    binding = {
        "event": "run_binding",
        "schema_version": 1,
        "initial_head": "c" * 40,
        "config_sha256": "f" * 64,
        "backend": "codex",
    }
    raw = json.dumps(binding).encode() + b"\n" + journal.read_bytes()
    journal.write_bytes(raw)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
    store_state(tmp_path, state)
    summary = summarize_execution(tmp_path)
    assert summary["recorded_status"] == status
    assert summary["diagnostics"] == []


@pytest.mark.parametrize(
    "change", ["event", "version", "digest", "head", "backend", "duplicate"]
)
def test_status_rejects_malformed_run_binding_prefix(
    tmp_path: Path, change: str
) -> None:
    state = write_history(tmp_path)
    journal = tmp_path / "events.jsonl"
    binding: dict[str, object] = {
        "event": "run_binding",
        "schema_version": 1,
        "initial_head": "c" * 40,
        "config_sha256": "f" * 64,
        "backend": "claude",
    }
    if change == "event":
        binding["event"] = "arbitrary_prefix"
    elif change == "version":
        binding["schema_version"] = True
    elif change == "digest":
        binding["config_sha256"] = "not-a-digest"
    elif change == "head":
        binding["initial_head"] = "a" * 40
    elif change == "backend":
        binding["backend"] = "other"
    prefix = json.dumps(binding).encode() + b"\n"
    raw = prefix * (2 if change == "duplicate" else 1) + journal.read_bytes()
    journal.write_bytes(raw)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
    store_state(tmp_path, state)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "bool_count",
        "path",
        "digest",
        "id",
        "revision",
        "verification",
        "output",
        "loop",
        "elapsed",
        "status",
        "active_missing",
        "count",
        "identity",
        "before_head",
    ],
)
def test_status_inconsistent_or_unsupported_metadata_is_unknown(
    tmp_path: Path, change: str
) -> None:
    state = write_history(tmp_path)
    attempt = state["attempt_summary"]
    active = attempt["active_attempt"]
    if change == "schema":
        attempt["schema_version"] = 2
    elif change == "bool_count":
        attempt["attempt_count"] = True
    elif change == "path":
        attempt["journal"]["path"] = "/other/events.jsonl"
    elif change == "digest":
        attempt["journal"]["sha256"] = "0" * 64
    elif change == "id":
        active["attempt_id"] = "other"
    elif change == "revision":
        active["after_head"] = "f" * 40
    elif change == "verification":
        active["verification"]["overall_kind"] = "fail"
    elif change == "output":
        active["outputs"]["stdout"]["identifier"] = "0" * 64
    elif change == "loop":
        state["loop_summary"]["reason"] = "different"
    elif change == "elapsed":
        state["loop_summary"]["elapsed_seconds"] = True
    elif change == "status":
        state["loop_summary"]["status"] = ["pass"]
    elif change == "active_missing":
        attempt["active_attempt"] = None
    elif change == "count":
        attempt["attempt_count"] = 0
    elif change == "identity":
        active["repository_id"] = "other"
    elif change == "before_head":
        active["before_head"] = "f" * 40
    store_state(tmp_path, state)
    result = summarize_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert result["diagnostics"]
    assert result["attempt_ids"] == []


@pytest.mark.parametrize("leaf", ["current.json", "events.jsonl"])
@pytest.mark.parametrize(
    "data",
    [
        b"PRIVATE-MARKER",
        b"\xff",
        b"[]\n",
        b'{"duplicate":1,"duplicate":2}\n',
        b'{"x":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}\n",
    ],
)
def test_status_malformed_input_has_fixed_unknown_diagnostics(
    tmp_path: Path, leaf: str, data: bytes
) -> None:
    write_history(tmp_path)
    (tmp_path / leaf).write_bytes(data)
    result = summarize_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert "PRIVATE-MARKER" not in json.dumps(result)


@pytest.mark.parametrize("hazard", ["symlink", "hardlink", "fifo", "directory"])
@pytest.mark.parametrize("leaf", ["current.json", "events.jsonl"])
def test_status_refuses_unsafe_leaf_without_writes(
    tmp_path: Path, hazard: str, leaf: str
) -> None:
    write_history(tmp_path)
    path = tmp_path / leaf
    data = path.read_bytes()
    target = tmp_path / "target"
    target.write_bytes(data)
    path.unlink()
    if hazard == "symlink":
        path.symlink_to(target)
    elif hazard == "hardlink":
        os.link(target, path)
    elif hazard == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"
    assert target.read_bytes() == data


def test_status_refuses_symlink_ancestor(tmp_path: Path) -> None:
    state = tmp_path / "real"
    write_history(state)
    link = tmp_path / "alias"
    link.symlink_to(state, target_is_directory=True)
    assert summarize_execution(link)["recorded_status"] == "unknown"


@pytest.mark.parametrize(
    ("leaf", "limit"),
    [("current.json", 1024 * 1024), ("events.jsonl", 8 * 1024 * 1024)],
)
def test_status_refuses_oversize_before_read(
    tmp_path: Path, leaf: str, limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_history(tmp_path)
    with (tmp_path / leaf).open("wb") as stream:
        stream.truncate(limit + 1)
    original_read = os.read

    def bounded_read(fd: int, count: int) -> bytes:
        assert os.fstat(fd).st_size <= limit or leaf == "events.jsonl"
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", bounded_read)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


def test_status_refuses_too_many_or_incomplete_records(tmp_path: Path) -> None:
    state = write_history(tmp_path)
    (tmp_path / "events.jsonl").write_bytes(b"{}\n" * 10001)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"
    store_state(tmp_path, state)
    (tmp_path / "events.jsonl").write_bytes(b"{}")
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


def test_status_detects_pair_replacement_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_history(tmp_path)
    original_read = os.read
    changed = False

    def mutate(fd: int, count: int) -> bytes:
        nonlocal changed
        data = original_read(fd, count)
        if not changed:
            changed = True
            path = tmp_path / "current.json"
            payload = path.read_bytes()
            path.unlink()
            path.write_bytes(payload)
        return data

    monkeypatch.setattr(os, "read", mutate)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


def test_status_stale_prefix_and_missing_terminal_stay_unknown(tmp_path: Path) -> None:
    state = write_history(tmp_path)
    journal = tmp_path / "events.jsonl"
    rows = journal.read_bytes().splitlines(keepends=True)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(
        b"".join(rows[:1])
    ).hexdigest()
    store_state(tmp_path, state)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"
    write_history(tmp_path)
    journal.write_bytes(b"".join(rows[:-1]))
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


@pytest.mark.parametrize("mutation", ["growth", "truncation"])
def test_status_detects_file_change_during_pair_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    write_history(tmp_path)
    original_read = os.read
    changed = False

    def mutate(fd: int, count: int) -> bytes:
        nonlocal changed
        data = original_read(fd, count)
        if not changed:
            changed = True
            path = tmp_path / "events.jsonl"
            with path.open("ab" if mutation == "growth" else "wb") as stream:
                stream.write(b"\n")
        return data

    monkeypatch.setattr(os, "read", mutate)
    assert summarize_execution(tmp_path)["recorded_status"] == "unknown"


def test_status_no_verification_nonpass_is_supported(tmp_path: Path) -> None:
    state = write_history(tmp_path, "stopped")
    rows = (tmp_path / "events.jsonl").read_bytes().splitlines(keepends=True)
    prefix = b"".join(rows[:-2])
    state["attempt_summary"]["active_attempt"]["verification"] = None
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(prefix).hexdigest()
    store_state(tmp_path, state)
    (tmp_path / "events.jsonl").write_bytes(prefix + rows[-1])
    result = summarize_execution(tmp_path)
    assert result["recorded_status"] == "stopped"
    assert result["verification"] is None


def test_status_never_opens_output_or_repository_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_history(tmp_path)
    original_open = os.open
    allowed = set(tmp_path.parts) | {tmp_path.anchor, "current.json", "events.jsonl"}

    def limited(
        path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        assert str(path) in allowed
        assert not flags & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", limited)
    assert summarize_execution(tmp_path)["recorded_status"] == "pass"


def write_null_loop_history(directory: Path) -> dict[str, Any]:
    """Persist the producer's baseline-protection stop, before any turn intent."""
    start = {"event": "loop_start", "repository_id": "recorded-repo"}
    protected = {
        "event": "loop_protected_inputs",
        "attempt_id": None,
        "checks": [{"path_sha256": "a" * 64, "state": "modified"}],
    }
    outcome = {
        "event": "loop_outcome",
        "status": "blocked",
        "reason": "protected_input_changed",
        "commands": 1,
        "elapsed_seconds": 0.2,
        "elapsed_boundary": "before_outcome_persistence",
        "attempt_ids": [],
    }
    prefix = b"".join(json.dumps(row).encode() + b"\n" for row in (start, protected))
    state = {
        "attempt_summary": {
            "schema_version": 1,
            "attempt_count": 0,
            "journal": {
                "path": str(directory / "events.jsonl"),
                "sha256": hashlib.sha256(prefix).hexdigest(),
            },
            "active_attempt": None,
        },
        "loop_summary": outcome,
    }
    (directory / "events.jsonl").write_bytes(
        prefix + json.dumps(outcome).encode() + b"\n"
    )
    store_state(directory, state)
    return state


@pytest.mark.parametrize("binding", ["prefix", "full"])
def test_status_loop_protected_null_before_attempt_preserves_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], binding: str
) -> None:
    from agent_harness.cli import main

    state = write_null_loop_history(tmp_path)
    if binding == "full":
        state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(
            (tmp_path / "events.jsonl").read_bytes()
        ).hexdigest()
        store_state(tmp_path, state)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    result = summarize_execution(tmp_path)
    assert result["recorded_status"] == "blocked"
    assert result["recorded_reason"] == "protected_input_changed"
    assert result["attempt_ids"] == []
    assert result["target_revision"] is None
    assert result["diagnostics"] == []
    assert result["historical_only"] is True
    assert result["current_workspace_verified"] is False
    assert main(["status", "--state-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("\n") == 1
    assert json.loads(output.out)["recorded_status"] == "blocked"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "context", ["unknown-event", "after-attempt", "unknown-attempt-id"]
)
def test_status_loop_protected_null_rejects_invalid_contexts(
    tmp_path: Path, context: str
) -> None:
    if context == "after-attempt":
        state = write_history(tmp_path, "human")
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
        ]
        events.insert(
            -1,
            {
                "event": "loop_protected_inputs",
                "attempt_id": None,
                "checks": [{"path_sha256": "a" * 64, "state": "modified"}],
            },
        )
    else:
        state = write_null_loop_history(tmp_path)
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
        ]
        if context == "unknown-event":
            events[1]["event"] = "unknown_nullable_event"
        else:
            events[1]["attempt_id"] = "unknown-attempt"
    prefix = b"".join(json.dumps(event).encode() + b"\n" for event in events[:-1])
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(prefix).hexdigest()
    (tmp_path / "events.jsonl").write_bytes(
        prefix + json.dumps(events[-1]).encode() + b"\n"
    )
    store_state(tmp_path, state)

    result = summarize_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert result["diagnostics"]

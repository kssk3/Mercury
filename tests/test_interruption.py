"""R3 contract: evidence-only reports and current-report human requests."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from agent_harness.journal import EventJournal
from agent_harness.state import CurrentStateStore


def api() -> Any:
    assert importlib.util.find_spec("agent_harness.interruption") is not None, (
        "R3 read-only interruption report and human gate are missing"
    )
    from agent_harness import interruption

    return interruption


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, bytes, bytes, str]:
    # Existing producer runs a local fake executable, never the Codex service.
    from test_startup import build_completed_loop

    directory = tmp_path_factory.mktemp("r3-seed")
    root = directory / "repo"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
    (root / "user.txt").write_text("original")
    root, journal, store, digest = build_completed_loop(root, directory, True)
    return root, journal.path.read_bytes(), store.path.read_bytes(), digest


@pytest.fixture
def evidence(tmp_path: Path, seed: tuple[Path, bytes, bytes, str]) -> tuple[Any, ...]:
    from test_startup import rewrite_runtime_events

    original, raw, state, digest = seed
    root = (tmp_path / "repo").resolve()
    shutil.copytree(original, root)
    old_id = hashlib.sha256(os.fsencode(original)).hexdigest()
    new_id = hashlib.sha256(os.fsencode(root)).hexdigest()

    def rebind(value: bytes) -> Any:
        return json.loads(
            value.decode()
            .replace(str(original.parent), str(tmp_path))
            .replace(old_id, new_id)
        )

    journal, store = (
        EventJournal(tmp_path / "external"),
        CurrentStateStore(tmp_path / "external"),
    )
    store.write(rebind(state))
    fixture = root, journal, store, digest
    rewrite_runtime_events(fixture, [rebind(line) for line in raw.splitlines()])
    return fixture


def report(fixture: tuple[Any, ...]) -> Any:
    root, journal, store, digest = fixture
    return api().report_interruption(
        root, journal=journal, state_store=store, expected_contract_sha256=digest
    )


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_report_preserves_real_completion_failure_and_never_writes(
    evidence: tuple[Any, ...],
) -> None:
    root, journal, store, _ = evidence
    (root / "staged.txt").write_text("user staged")
    subprocess.run(
        ["git", "-C", str(root), "add", "staged.txt"], check=True, timeout=10
    )
    before = snapshot(root.parent)
    result = report(evidence)
    assert result.evidence.status == "interrupted"
    assert result.evidence.workspace_delta.added == ("staged.txt",)
    assert any(
        f.reason == "native_nonzero" for f in result.evidence.failures[0].findings
    )
    assert result.evidence.accounting_complete
    assert report(evidence) == result
    for action in ("stop", "inspect", "new_attempt", "recover_patch"):
        decision = api().human_gate(
            result, {"report_sha256": result.report_sha256, "action": action}
        )
        assert not decision.execution_authorized
        assert decision.status == (
            action if action in ("stop", "inspect") else "requested"
        )
    assert snapshot(root.parent) == before
    with pytest.raises(FrozenInstanceError):
        result.report_sha256 = "b" * 64


def test_completed_fresh_unknown_are_distinct(
    evidence: tuple[Any, ...], tmp_path: Path
) -> None:
    completed = report(evidence)
    assert completed.evidence.status == "completed"
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "--quiet", str(empty)], check=True, timeout=10)
    fresh = report(
        (
            empty,
            EventJournal(tmp_path / "absent"),
            CurrentStateStore(tmp_path / "absent"),
            "a" * 64,
        )
    )
    assert fresh.evidence.status == "fresh"
    assert not (tmp_path / "absent").exists()
    journal = evidence[1]
    with journal.path.open("ab") as stream:
        stream.write(b"{private-secret")
    unknown = report(evidence)
    assert unknown.evidence.status == "unknown"
    assert unknown.evidence.attempt_ids == completed.evidence.attempt_ids
    assert unknown.evidence.failures == completed.evidence.failures
    assert "private-secret" not in repr(unknown)
    for action in ("new_attempt", "recover_patch"):
        assert (
            api()
            .human_gate(
                unknown, {"report_sha256": unknown.report_sha256, "action": action}
            )
            .status
            == "blocked"
        )


@pytest.mark.parametrize(
    "drift", ["workspace", "journal", "state", "contract", "repository"]
)
def test_identity_drift_rejects_old_response(
    evidence: tuple[Any, ...], tmp_path: Path, drift: str
) -> None:
    previous = report(evidence)
    root, journal, store, digest = evidence
    if drift == "workspace":
        (root / "user.txt").write_text("changed")
    elif drift == "journal":
        journal.append({"event": "note", "value": "safe unrelated metadata"})
    elif drift == "state":
        value = store.read()
        assert value is not None
        store.write({**value, "note": "changed"})
    elif drift == "contract":
        digest = "c" * 64
    else:
        root = tmp_path / "other"
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
    current = report((root, journal, store, digest))
    assert current.report_sha256 != previous.report_sha256
    assert (
        api()
        .human_gate(
            current, {"report_sha256": previous.report_sha256, "action": "inspect"}
        )
        .status
        == "blocked"
    )


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"report_sha256": "wrong", "action": "inspect"},
        {"action": 1},
        {"action": "native_resume"},
        {"action": "delete"},
        {"action": "inspect", "extra": True},
    ],
)
def test_malformed_or_unsupported_response_blocks(
    evidence: tuple[Any, ...], response: object
) -> None:
    current = report(evidence)
    if isinstance(response, dict) and "report_sha256" not in response:
        response = {"report_sha256": current.report_sha256, **response}
    result = api().human_gate(current, response)
    assert result.status == "blocked"
    assert not result.execution_authorized


def test_invalid_report_blocks_current_response(evidence: tuple[Any, ...]) -> None:
    current = report(evidence)
    invalid = replace(current, expected_contract_sha256="invalid")
    assert (
        api()
        .human_gate(
            invalid, {"report_sha256": invalid.report_sha256, "action": "inspect"}
        )
        .status
        == "blocked"
    )


def test_unavailable_evidence_keeps_repository_and_selector_binding(
    tmp_path: Path,
) -> None:
    roots = (tmp_path / "one", tmp_path / "two")
    reports = []
    for root in roots:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
        reports.append(
            report(
                (
                    root,
                    EventJournal(root / "unsafe"),
                    CurrentStateStore(root / "unsafe"),
                    "a" * 64,
                )
            )
        )
    first, second = reports
    assert first.evidence.status == second.evidence.status == "unknown"
    assert first.evidence_sha256 is second.evidence_sha256 is None
    assert first.report_sha256 != second.report_sha256
    assert first.repository_id != second.repository_id
    alternative = report(
        (
            roots[0],
            EventJournal(roots[0] / "other"),
            CurrentStateStore(roots[0] / "other"),
            "a" * 64,
        )
    )
    assert alternative.report_sha256 != first.report_sha256
    assert alternative.repository_id == first.repository_id
    for current in (second, alternative):
        for action in ("stop", "inspect"):
            decision = api().human_gate(
                current, {"report_sha256": first.report_sha256, "action": action}
            )
            assert decision.status == "blocked"
            assert not decision.execution_authorized
        assert (
            api()
            .human_gate(
                current, {"report_sha256": current.report_sha256, "action": "inspect"}
            )
            .status
            == "inspect"
        )
        for action in ("new_attempt", "recover_patch"):
            assert (
                api()
                .human_gate(
                    current, {"report_sha256": current.report_sha256, "action": action}
                )
                .status
                == "blocked"
            )
    assert all(not (root / "unsafe").exists() for root in roots)
    assert not (roots[0] / "other").exists()
    assert str(tmp_path) not in repr(reports)

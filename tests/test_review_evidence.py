"""Actual candidate identity and recorded-evidence invalidation contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_review import _evidence

from agent_harness.attempt import _command_result
from agent_harness.journal import EventJournal
from agent_harness.review import ReviewBinding, ReviewReceipt, assess_review
from agent_harness.review_evidence import (
    build_review_evidence,
    candidate_sha256,
    read_review_binding,
)
from agent_harness.turn import FileFingerprint, GitStatusEntry, RepositoryObservation


def _records() -> tuple[RepositoryObservation, dict[str, Any]]:
    report = _evidence()
    root = str(report.repository_root)
    observation = RepositoryObservation(
        root,
        hashlib.sha256(root.encode()).hexdigest(),
        (FileFingerprint("value.py", "regular", "a" * 64, 1, False, 0o644),),
        report.target_revision,
        "c" * 64,
        (GitStatusEntry("value.py", " M"),),
        "refs/heads/main",
    )
    verification: dict[str, Any] = {
        "event": "attempt_verification",
        "schema_version": 2,
        "attempt_id": "attempt-1",
        "repository_root": root,
        "repository_id": observation.repository_id,
        "contract_sha256": "b" * 64,
        "context_sha256": "c" * 64,
        "before_head": report.target_revision,
        "target_revision": report.target_revision,
        "technical_kind": "pass",
        "overall_kind": "pass",
        "commands": [_command_result(result) for result in report.profile.results],
        "baseline_commands": [
            _command_result(result) for result in report.profile.baseline.results
        ],
        "reason_command_sha256": [],
        "protected_inputs": [],
        "output_identifiers": None,
    }
    return observation, verification


def _bound(
    observation: RepositoryObservation | None = None,
    verification: dict[str, Any] | None = None,
    settings: str = "d" * 64,
) -> ReviewBinding:
    original_observation, original_verification = _records()
    selected = original_observation if observation is None else observation
    event = original_verification if verification is None else verification
    return read_review_binding(event, build_review_evidence(event, selected, settings))


def test_review_binding_is_deterministic() -> None:
    observation, event = _records()
    reordered = dict(reversed(list(event.items())))
    assert _bound(observation, event) == _bound(observation, reordered)


def test_review_binding_changes_with_tracked_content() -> None:
    observation, _ = _records()
    changed = replace(
        observation, files=(replace(observation.files[0], sha256="e" * 64),)
    )
    assert changed.head == observation.head
    assert _bound(changed).candidate_sha256 != _bound().candidate_sha256


def test_review_binding_changes_with_untracked_content() -> None:
    observation, _ = _records()
    changed = replace(
        observation,
        files=observation.files
        + (FileFingerprint("new.py", "regular", "e" * 64, 3, False, 0o644),),
        status=observation.status + (GitStatusEntry("new.py", "??"),),
    )
    assert _bound(changed).candidate_sha256 != _bound().candidate_sha256


def test_review_binding_changes_with_deletion() -> None:
    observation, _ = _records()
    changed = replace(
        observation,
        files=(FileFingerprint("value.py", "missing", None, None, None),),
        status=(GitStatusEntry("value.py", " D"),),
    )
    assert _bound(changed).candidate_sha256 != _bound().candidate_sha256


def test_review_binding_changes_with_file_mode() -> None:
    observation, _ = _records()
    changed = replace(
        observation,
        files=(replace(observation.files[0], mode=0o755, executable=True),),
    )
    assert _bound(changed).candidate_sha256 != _bound().candidate_sha256


def test_review_binding_changes_with_index() -> None:
    observation, _ = _records()
    assert (
        _bound(replace(observation, index_sha256="f" * 64)).candidate_sha256
        != _bound().candidate_sha256
    )


def test_review_binding_changes_with_contract() -> None:
    _, event = _records()
    event["contract_sha256"] = "f" * 64
    assert _bound(verification=event).contract_sha256 != _bound().contract_sha256
    assert _bound(verification=event).evidence_sha256 != _bound().evidence_sha256


def test_review_binding_changes_with_attempt() -> None:
    _, event = _records()
    event["attempt_id"] = "another-attempt"
    assert _bound(verification=event).evidence_sha256 != _bound().evidence_sha256


def test_review_binding_changes_with_verification_results() -> None:
    _, event = _records()
    event["commands"][0]["exit_code"] = 1
    assert _bound(verification=event).evidence_sha256 != _bound().evidence_sha256


def test_review_binding_changes_with_baseline() -> None:
    _, event = _records()
    event["baseline_commands"][0]["exit_code"] = 1
    assert _bound(verification=event).evidence_sha256 != _bound().evidence_sha256


def test_review_binding_changes_with_verification_settings() -> None:
    assert _bound(settings="f" * 64).evidence_sha256 != _bound().evidence_sha256


def test_review_binding_preserves_protected_failure() -> None:
    binding = _bound()
    receipt = ReviewReceipt(binding, "reviewer", "opaque-report", "completed")
    assessment = assess_review(_evidence("protected"), binding, receipt)
    assert assessment.technical_kind.value == "fail"
    assert not assessment.conditions_satisfied


def test_review_binding_round_trip() -> None:
    observation, event = _records()
    stored = json.loads(json.dumps(build_review_evidence(event, observation, "d" * 64)))
    assert read_review_binding(event, stored) == _bound()


def test_review_binding_rejects_malformed_record() -> None:
    observation, event = _records()
    for malformed in (None, {}, {"schema_version": True}):
        with pytest.raises(ValueError):
            read_review_binding(event, malformed)
    stored = build_review_evidence(event, observation, "d" * 64)
    stored["binding"] = {"candidate_sha256": "f" * 64}
    with pytest.raises(ValueError):
        read_review_binding(event, stored)


def test_review_binding_rejects_unsupported_version() -> None:
    observation, event = _records()
    stored = build_review_evidence(event, observation, "d" * 64)
    stored["schema_version"] = 2
    with pytest.raises(ValueError):
        read_review_binding(event, stored)


def test_review_binding_rejects_candidate_mismatch() -> None:
    observation, event = _records()
    stored = json.loads(json.dumps(build_review_evidence(event, observation, "d" * 64)))
    stored["candidate"]["head"] = "f" * 40
    with pytest.raises(ValueError):
        read_review_binding(event, stored)


def test_review_binding_invalidates_stale_receipt() -> None:
    observation, _ = _records()
    old = _bound()
    changed = replace(
        observation, files=(replace(observation.files[0], sha256="e" * 64),)
    )
    receipt = ReviewReceipt(old, "reviewer", "opaque-report", "completed")
    assessment = assess_review(_evidence(), _bound(changed), receipt)
    assert assessment.review_kind == "invalid"
    assert not assessment.conditions_satisfied


def test_review_binding_missing_facts_do_not_clear() -> None:
    observation, event = _records()
    del event["commands"]
    with pytest.raises((KeyError, ValueError)):
        build_review_evidence(event, observation, "d" * 64)
    assessment = assess_review(_evidence(), _bound(), None)
    assert not assessment.conditions_satisfied


def test_review_binding_conversion_has_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    observation, event = _records()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("binding conversion attempted filesystem access")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    stored = build_review_evidence(event, observation, "d" * 64)
    assert read_review_binding(event, stored).candidate_sha256 == candidate_sha256(
        observation
    )


def test_review_binding_continuation_does_not_prepare_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_verification_continuation import (
        test_development_checkpoint_continues_only_final,
    )

    test_development_checkpoint_continues_only_final(tmp_path, monkeypatch, "none")
    event = next(
        event
        for event in EventJournal(tmp_path / "state").read()
        if event["event"] == "attempt_verification"
    )
    assert event["schema_version"] == 2
    assert (
        read_review_binding(event, event["review_binding"]).attempt_id
        == event["attempt_id"]
    )

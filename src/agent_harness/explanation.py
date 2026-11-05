"""Read-only explanation of one bounded historical snapshot; never authority."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_harness import __version__
from agent_harness.attempt import _command_digest, _process
from agent_harness.contract import TaskContract
from agent_harness.inspection import (
    _IDENTITY,
    _decode,
    _digest,
    _Invalid,
    _mapping,
    _project,
    _read_pair,
    _require,
    _unknown,
    _verification,
)
from agent_harness.inspection import (
    _text as _record_text,
)
from agent_harness.loop import CriterionBinding, bind_completion_criteria
from agent_harness.policy import VerificationPolicy
from agent_harness.run_config import _absolute, _object, _read_config, _strings, _text


def _commands(verification: dict[str, object] | None) -> list[dict[str, object]]:
    if verification is None:
        return []
    return [
        {key: row[key] for key in ("command_sha256", "exit_code", "timed_out")}
        for row in map(_mapping, cast(list[object], verification["commands"]))
    ]


def _attempts(events: list[dict[str, object]]) -> list[dict[str, object]]:
    attempts = []
    for intent in (event for event in events if event.get("event") == "turn_intent"):
        identity = {key: intent[key] for key in _IDENTITY if key != "before_head"}
        identity["before_head"] = _mapping(intent["before"])["head"]
        for key in ("attempt_id", "repository_root", "repository_id"):
            _record_text(identity[key])
        for key in ("contract_sha256", "context_sha256"):
            _digest(identity[key])
        if identity["before_head"] is not None:
            _digest(identity["before_head"], (40, 64))
        relevant = [e for e in events if e.get("attempt_id") == intent["attempt_id"]]
        observations = [
            e
            for e in relevant
            if e.get("event")
            in {
                "turn_observation",
                "callback_observation",
                "turn_observation_failed",
                "callback_observation_failed",
            }
        ]
        _require(bool(observations))
        _require(
            observations[0]["event"] in {"turn_observation", "turn_observation_failed"}
        )
        previous_process: dict[str, object] | None = None
        failed = False
        for index, observation in enumerate(observations):
            kind = observation["event"]
            _require(not failed)
            if index:
                _require(
                    kind in {"callback_observation", "callback_observation_failed"}
                )
            failed = kind in {"turn_observation_failed", "callback_observation_failed"}
            if kind in {"turn_observation", "callback_observation"}:
                after = _mapping(observation["after"])
                for key in ("repository_root", "repository_id"):
                    _require(after[key] == identity[key])
                if after["head"] is not None:
                    _digest(after["head"], (40, 64))
                _require(observation.get("process") is not None)
            elif kind == "turn_observation_failed":
                classification = observation["classification"]
                _require(
                    classification in {"adapter_raised", "after_observation_failed"}
                )
                _require(
                    (observation.get("process") is None)
                    == (classification == "adapter_raised")
                )
            else:
                _require(observation["classification"] == "after_observation_failed")
                _require(observation["repository_id"] == identity["repository_id"])
                _require(observation.get("process") is not None)
            if observation.get("process") is not None:
                process = _process(observation["process"])
                _require(previous_process is None or process == previous_process)
                previous_process = process
        observed = observations[-1]
        revision = None
        native: dict[str, object] = {"process_status": "unknown", "exit_code": None}
        if observed is not None:
            if observed["event"] in {"turn_observation", "callback_observation"}:
                after = _mapping(observed["after"])
                for key in ("repository_root", "repository_id"):
                    _require(after[key] == identity[key])
                revision = after["head"]
            if observed.get("process") is not None:
                process = _process(observed["process"])
                native = {
                    key: process[key]
                    for key in (
                        "process_status",
                        "exit_code",
                        "duration_seconds",
                        "final_output_status",
                    )
                }
        saved = [e for e in relevant if e.get("event") == "attempt_verification"]
        _require(len(saved) <= 1)
        verification = _verification(saved[0]) if saved else None
        binding = None
        if verification is not None:
            _require(all(verification[key] == identity[key] for key in _IDENTITY))
            _require(verification["target_revision"] == revision)
            if verification["schema_version"] == 2:
                binding = _mapping(verification["review_binding"])["binding"]
        attempts.append(
            {
                **identity,
                "target_revision": revision,
                "candidate_binding": binding,
                "native_process": native,
                "checker_result": None
                if verification is None
                else {
                    "technical_kind": verification["technical_kind"],
                    "overall_kind": verification["overall_kind"],
                },
                "commands": _commands(verification),
                "journal_event_indices": [
                    index
                    for index, event in enumerate(events)
                    if event.get("attempt_id") == intent["attempt_id"]
                ],
                "evidence_state": "unverified"
                if verification is None
                else "recorded_metadata",
            }
        )
    return attempts


def _config(
    path: Path | str | None,
    events: list[dict[str, object]],
    attempts: list[dict[str, object]],
) -> tuple[dict[str, object], tuple[CriterionBinding, ...]]:
    if path is None:
        return {"state": "not_supplied", "sha256": None}, ()
    config_path = _absolute(str(path))
    raw = _read_config(config_path)
    digest = hashlib.sha256(raw).hexdigest()
    receipt: dict[str, object] = {"path": str(config_path), "sha256": digest}
    binding = events[0]
    if binding.get("event") != "run_binding":
        return {**receipt, "state": "unbound"}, ()
    if binding["config_sha256"] != digest:
        return {**receipt, "state": "digest_mismatch"}, ()
    data = _decode(raw)
    _require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    contract_data = _object(
        data["contract"],
        {
            "goal",
            "allowed_paths",
            "protected_paths",
            "completion_criteria",
        },
    )
    contract = TaskContract(
        _text(contract_data["goal"]),
        _strings(contract_data["allowed_paths"]),
        _strings(contract_data["protected_paths"]),
        _strings(contract_data["completion_criteria"]),
    )
    contract_digest = hashlib.sha256(contract.to_json().encode()).hexdigest()
    if not attempts or any(a["contract_sha256"] != contract_digest for a in attempts):
        return {**receipt, "state": "contract_mismatch"}, ()
    verification = _object(
        data["verification"],
        {
            "commands",
            "working_directory",
            "timeout_seconds",
        },
    )
    commands = verification["commands"]
    _require(isinstance(commands, list) and 1 <= len(commands) <= 8)
    _require(type(verification["timeout_seconds"]) is int)
    policy = VerificationPolicy(
        tuple(_strings(command) for command in cast(list[object], commands)),
        _text(verification["working_directory"]),
        cast(int, verification["timeout_seconds"]),
    )
    bindings = bind_completion_criteria(contract, policy, data["criterion_commands"])
    return {**receipt, "state": "bound", "contract_sha256": contract_digest}, bindings


def _criteria(
    bindings: tuple[CriterionBinding, ...], attempts: list[dict[str, object]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for binding in bindings:
        hashes = [_command_digest(command) for command in binding.commands]
        decisions = []
        for attempt in attempts:
            commands = cast(list[dict[str, object]], attempt["commands"])
            outcomes = [row for row in commands if row["command_sha256"] in hashes]
            complete = all(
                any(row["command_sha256"] == digest for row in outcomes)
                for digest in hashes
            )
            state = "unverified"
            if complete:
                if any(row["timed_out"] for row in outcomes):
                    state = "timeout"
                elif any(row["exit_code"] is None for row in outcomes):
                    state = "unknown"
                else:
                    state = (
                        "pass"
                        if all(row["exit_code"] == 0 for row in outcomes)
                        else "fail"
                    )
            decisions.append(
                {
                    "attempt_id": attempt["attempt_id"],
                    "saved_command_outcome": state,
                    "commands": outcomes,
                }
            )
        rows.append(
            {
                "criterion": binding.criterion,
                "command_sha256": hashes,
                "mode": "automatic_mapping",
                "attempts": decisions,
            }
        )
    return rows


def _comparison(expected: str | None, saved: object, lengths: tuple[int, ...]) -> str:
    if expected is None:
        return "not_requested"
    _digest(expected, lengths)
    return (
        "unavailable" if saved is None else "match" if expected == saved else "mismatch"
    )


def _provenance(
    summary: dict[str, object],
    events: list[dict[str, object]],
    attempts: list[dict[str, object]],
    config: dict[str, object],
    comparison: dict[str, object],
) -> dict[str, object]:
    """Project only validated captured facts; absent receipts remain unavailable."""
    valid = not summary["diagnostics"]
    evidence = _mapping(summary["evidence"]) if valid else {}
    cards: list[dict[str, object]] = []
    for key in ("current", "journal", "config"):
        source = config if key == "config" and valid else evidence.get(key)
        receipt = _mapping(source) if source is not None else {}
        cards.append(
            {
                "id": key,
                "origin": "operator_supplied_config"
                if key == "config"
                else "harness_journal",
                "path": receipt.get("path"),
                "sha256": receipt.get("sha256"),
                "state": "captured" if receipt.get("sha256") else "unavailable",
                "digest_kind": "captured_bytes",
                "binding_state": config["state"] if key == "config" else None,
            }
        )
    cards.append(
        {
            "id": "outputs",
            "origin": "harness_journal",
            "state": "recorded_metadata" if evidence.get("outputs") else "unavailable",
            "digest_kind": "recorded_identifier_payload_not_read",
            "identifiers": evidence.get("outputs"),
        }
    )
    targets: list[dict[str, object]] = []
    for attempt in attempts if valid else []:
        binding = attempt["candidate_binding"]
        targets.append(
            {
                **{
                    key: attempt[key]
                    for key in (
                        "attempt_id",
                        "repository_root",
                        "repository_id",
                        "before_head",
                        "target_revision",
                        "contract_sha256",
                        "context_sha256",
                        "commands",
                        "native_process",
                        "checker_result",
                        "journal_event_indices",
                    )
                },
                "candidate_sha256": None
                if binding is None
                else _mapping(binding)["candidate_sha256"],
                "comparison": comparison
                if attempt is attempts[-1]
                else {
                    "context_only": True,
                    "revision": "not_requested",
                    "candidate_sha256": "not_requested",
                },
                "current_workspace_verified": False,
            }
        )
    process_available = any(
        _mapping(target["native_process"])["process_status"] != "unknown"
        for target in targets
    )
    checks_available = any(target["checker_result"] is not None for target in targets)
    return {
        "schema_version": 1,
        "record_availability": {
            "saved_execution": "recorded_metadata"
            if process_available
            else "unavailable",
            "saved_checks": "recorded_metadata" if checks_available else "unavailable",
        },
        "ownership": {
            "saved_execution": "harness_journal",
            "saved_checks": "harness_checker",
            "external_operator_checks": "not_ingested",
            "independent_reviews": "not_ingested",
        },
        "execution": {
            "configured_backend": events[0]["backend"]
            if valid and events and events[0].get("event") == "run_binding"
            else None,
            "native_launch_count": None,
            "model_identity": None,
            "started_at": None,
            "ended_at": None,
            "environment_identity": None,
            "tool_use": "unavailable",
            "receipt_state": "saved_process_metadata_only"
            if process_available
            else "unavailable",
        },
        "targets": targets,
        "evidence": cards,
    }


def explain_execution(
    state_directory: Path | str,
    *,
    config_path: Path | str | None = None,
    expected_revision: str | None = None,
    expected_candidate_sha256: str | None = None,
    external_checks: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Compose saved facts from one byte pair; expected values are caller context.

    No live repository, output payload, execution preflight, approval or receipt
    assessment is read or invoked. Config inspection uses only pure validators.
    """
    events: list[dict[str, object]] = []
    if isinstance(external_checks, str):
        external_checks = (external_checks,)
    attempts: list[dict[str, object]] = []
    try:
        path, current, journal = _read_pair(state_directory)
        summary = _project(path, current, journal)
        events = [_decode(line) for line in journal.splitlines()]
        attempts = _attempts(events)
    except _Invalid as error:
        summary = _unknown(str(error))
    except FileNotFoundError:
        summary = _unknown("missing_input")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        summary = _unknown("unreadable_or_invalid_history")
    blockers = list(cast(list[str], summary["diagnostics"]))
    config: dict[str, object] = {"state": "unverified", "sha256": None}
    bindings: tuple[CriterionBinding, ...] = ()
    if not blockers:
        try:
            config, bindings = _config(config_path, events, attempts)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ):
            config = {"state": "unreadable_or_invalid_config", "sha256": None}
        if config["state"] != "bound":
            blockers.append("config_" + cast(str, config["state"]))
    binding = summary["review_binding"]
    candidate = None if binding is None else _mapping(binding)["candidate_sha256"]
    if candidate is None:
        blockers.append("candidate_content_unavailable")
    comparison: dict[str, object] = {"context_only": True}
    for key, expected, saved, lengths in (
        ("revision", expected_revision, summary["target_revision"], (40, 64)),
        ("candidate_sha256", expected_candidate_sha256, candidate, (64,)),
    ):
        try:
            comparison[key] = _comparison(expected, saved, lengths)
        except ValueError:
            comparison[key] = "invalid_expected_value"
        if comparison[key] not in {"match", "not_requested"}:
            blockers.append(key + "_" + cast(str, comparison[key]))
    criteria = _criteria(bindings, attempts)
    if criteria and any(
        not row["attempts"]
        or cast(list[dict[str, object]], row["attempts"])[-1]["saved_command_outcome"]
        != "pass"
        for row in criteria
    ):
        blockers.append("active_criteria_not_passed")
    ready = not blockers and summary["recorded_status"] == "pass"
    conclusion = "review_pending" if ready else "evidence_incomplete"
    if summary["recorded_status"] != "pass":
        conclusion = "recorded_" + cast(str, summary["recorded_status"])
        blockers.append("recorded_execution_not_passed")
    next_action = (
        "inspect_and_reconcile_saved_evidence" if blockers else "independent_review"
    )
    result: dict[str, object] = {
        **summary,
        "explanation": {
            "schema_version": 1,
            "inspector_version": __version__,
            "observed_at": datetime.now(UTC).isoformat(),
            "conclusion": conclusion,
            "evidence_readiness": "ready_for_manual_review" if ready else "incomplete",
            "blockers": blockers,
            "next": {"actor": "caller", "action": next_action},
            "comparison": comparison,
            "config": config,
            "provenance": _provenance(summary, events, attempts, config, comparison),
            "criteria": criteria,
            "attempts": attempts if not summary["diagnostics"] else [],
            "review": {"state": "implemented_but_unwired", "api": "assess_review"},
            "approval": {"state": "manual_not_recorded"},
            "limits": [
                "historical_metadata_only",
                "no_current_workspace_capture",
                "caller_comparison_is_not_freshness_proof",
                "no_authentication",
                "no_output_payload_verification",
                "no_review_receipt_integration",
                "no_execution_or_merge_authority",
                "no_cross_attempt_aggregation",
                "attempt_details_are_saved_metadata_not_full_execution_replay",
                "package_version_is_not_implementation_digest",
                "point_in_time_read_not_os_sandbox",
            ],
        },
    }
    if external_checks:
        from agent_harness.external_checks import read_external_check

        external: list[dict[str, object]] = []
        for receipt_path in external_checks[:32]:
            try:
                row = read_external_check(receipt_path)
                observed = row["after"]["observation"]
                historical = None if binding is None else _mapping(binding)
                applicability = "unavailable"
                if historical is not None:
                    applicability = (
                        "match"
                        if (
                            row["subject_state"] == "unchanged"
                            and row["protected_state"] == "unchanged"
                            and observed["repository_root"]
                            == historical["repository_root"]
                            and observed["head"] == summary["target_revision"]
                            and row["after"]["candidate_sha256"]
                            == historical["candidate_sha256"]
                        )
                        else "mismatch"
                    )
                external.append(
                    {
                        **row,
                        "state": "captured",
                        "historical_applicability": applicability,
                    }
                )
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
                RecursionError,
            ):
                external.append(
                    {
                        "origin": "external_command_receipt",
                        "state": "unavailable",
                        "outcome": "UNKNOWN",
                        "historical_applicability": "unavailable",
                        "reason": "invalid_or_incomplete_external_receipt",
                    }
                )
        if len(external_checks) > 32:
            external.append(
                {
                    "origin": "external_command_receipt",
                    "state": "unavailable",
                    "outcome": "UNKNOWN",
                    "reason": "external_receipt_limit",
                }
            )
        explanation = cast(dict[str, object], result["explanation"])
        explanation["external_checks"] = external
        provenance = _mapping(explanation["provenance"])
        _mapping(provenance["ownership"])["external_operator_checks"] = (
            "external_command_receipt"
        )
    return result

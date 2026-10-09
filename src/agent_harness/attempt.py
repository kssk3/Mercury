"""Append-only attempt metadata projections; caller owns serialization/B4 lock."""

from __future__ import annotations

import hashlib
import json
import math
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from agent_harness.adapter import TurnBudgets
from agent_harness.evidence import EvidenceReport
from agent_harness.journal import EventJournal
from agent_harness.repository import resolve_repository_root
from agent_harness.runner import CommandResult
from agent_harness.scope import _digest, _valid_path, _validate_observation
from agent_harness.state import CurrentStateStore
from agent_harness.turn import (
    FileFingerprint,
    GitStatusEntry,
    ObservationDelta,
    RepositoryObservation,
    _append,
    _validate_journal,
    compare_observations,
)


class AttemptJournal:
    """Read existing phases and explicitly refresh external current state.

    Reads create no files. Projections do not grant technical PASS or execution
    authority. There is no transaction, fsync, recovery or hostile-process claim.
    """

    def __init__(
        self,
        repository: Path | str,
        *,
        journal: EventJournal,
        state_store: CurrentStateStore,
    ) -> None:
        self.root = resolve_repository_root(repository)
        self.journal = journal
        self.state_store = state_store

    def read(self) -> tuple[dict[str, object], ...]:
        self._validate_persistence()
        try:
            return self._project(self.journal.read())
        except (KeyError, TypeError, ValueError, OverflowError):
            raise AttemptJournalError("invalid_history", False) from None

    def _project(
        self, events: list[dict[str, object]]
    ) -> tuple[dict[str, object], ...]:
        attempts: dict[str, dict[str, object]] = {}
        snapshots: dict[
            str, tuple[RepositoryObservation, RepositoryObservation | None]
        ] = {}
        for event in events:
            kind = event.get("event")
            if kind not in {
                "turn_intent",
                "turn_observation",
                "turn_observation_failed",
                "scope_decision",
                "native_output_stored",
                "attempt_verification",
            }:
                continue
            attempt_id = _text(event["attempt_id"])
            if kind == "turn_intent":
                _require(attempt_id not in attempts)
                before = _observation(event["before"])
                _require(event["repository_root"] == str(self.root))
                _require(event["repository_id"] == before.repository_id)
                _require(before.repository_root == str(self.root))
                _require(
                    _digest(event["contract_sha256"])
                    and _digest(event["context_sha256"])
                )
                _require(event["sandbox"] in {"workspace-write", "read-only"})
                budget = _mapping(event["budgets"])
                TurnBudgets(
                    cast(float, budget["timeout_seconds"]),
                    cast(int, budget["input_bytes"]),
                    cast(int, budget["stdout_bytes"]),
                    cast(int, budget["stderr_bytes"]),
                    cast(int, budget["final_bytes"]),
                )
                attempts[attempt_id] = {
                    "attempt_id": attempt_id,
                    "repository_root": str(self.root),
                    "repository_id": before.repository_id,
                    "contract_sha256": event["contract_sha256"],
                    "context_sha256": event["context_sha256"],
                    "before_head": before.head,
                    "after_head": None,
                    "observation": None,
                    "scope": None,
                    "outputs": None,
                    "verification": None,
                }
                snapshots[attempt_id] = (before, None)
                continue
            _require(attempt_id in attempts)
            entry = attempts[attempt_id]
            before, after = snapshots[attempt_id]
            if kind == "attempt_verification":
                _require(
                    entry["verification"] is None
                    and after is not None
                    and after.head is not None
                )
                entry["verification"] = _verification(event, entry)
                continue
            _require(entry["verification"] is None)
            if kind in {"turn_observation", "turn_observation_failed"}:
                _require(entry["observation"] is None)
                if kind == "turn_observation_failed":
                    _require(
                        event["classification"]
                        in {"adapter_raised", "after_observation_failed"}
                    )
                    process = _process(event["process"]) if "process" in event else None
                    _require(
                        (process is None)
                        == (event["classification"] == "adapter_raised")
                    )
                    entry["observation"] = {
                        "status": "failed",
                        "classification": event["classification"],
                        "process": process,
                    }
                else:
                    after = _observation(event["after"])
                    _require(after.repository_root == str(self.root))
                    _require(
                        _delta(event["delta"]) == compare_observations(before, after)
                    )
                    entry["after_head"] = after.head
                    entry["observation"] = {
                        "status": "observed",
                        "process": _process(event["process"]),
                    }
                    snapshots[attempt_id] = (before, after)
            else:
                _require(after is not None)
                _require(event["repository_id"] == before.repository_id)
                if kind == "scope_decision":
                    _require(entry["scope"] is None)
                    delta = _delta(event["delta"])
                    _require(
                        delta
                        == compare_observations(
                            before, cast(RepositoryObservation, after)
                        )
                    )
                    outside, protected = (
                        _paths(event["out_of_scope_paths"]),
                        _paths(event["protected_paths"]),
                    )
                    changed = set(delta.added + delta.modified + delta.deleted)
                    _require(
                        not set(outside).intersection(protected)
                        and set(outside + protected).issubset(changed)
                    )
                    unexplained = _boolean(event["unexplained_status_change"])
                    expected_unexplained = delta.status_changed and not (
                        delta.added
                        or delta.modified
                        or delta.deleted
                        or delta.head_changed
                        or delta.index_changed
                    )
                    _require(unexplained == bool(expected_unexplained))
                    proceed = _boolean(event["proceed"])
                    _require(
                        proceed
                        == (
                            not (
                                outside
                                or protected
                                or delta.head_changed
                                or delta.index_changed
                                or unexplained
                            )
                        )
                    )
                    entry["scope"] = {
                        "proceed": proceed,
                        "delta": {
                            key: list(value) if isinstance(value, tuple) else value
                            for key, value in asdict(delta).items()
                        },
                        "out_of_scope_paths": list(outside),
                        "protected_paths": list(protected),
                        "unexplained_status_change": unexplained,
                    }
                else:
                    _require(entry["scope"] is not None and entry["outputs"] is None)
                    entry["outputs"] = {
                        "stdout": _output(event["stdout"]),
                        "stderr": _output(event["stderr"]),
                        "final_message": None
                        if event["final_message"] is None
                        else _output(event["final_message"]),
                    }
                    process = _mapping(_mapping(entry["observation"])["process"])
                    outputs = _mapping(entry["outputs"])
                    for channel in ("stdout", "stderr", "final_message"):
                        capture = process[channel]
                        output = outputs[channel]
                        _require((capture is None) == (output is None))
                        if capture is not None:
                            captured = _mapping(capture)
                            stored = _mapping(output)
                            _require(
                                stored["captured_bytes"] == captured["retained_bytes"]
                            )
                            _require(
                                stored["capture_truncated"] == captured["truncated"]
                            )
        return tuple(attempts.values())

    def record_verification(
        self, attempt_id: str, report: EvidenceReport, *, refresh: bool = False
    ) -> dict[str, object]:
        """Append before optional refresh; supplied evidence is not authenticated.

        Identity binds the report root/revision to observed after HEAD, without
        proving a current dirty-worktree or completion-criterion match.
        """
        if not isinstance(report, EvidenceReport) or type(refresh) is not bool:
            raise AttemptJournalError("invalid_verification", False)
        entries = self.read()
        entry = next(
            (item for item in entries if item["attempt_id"] == attempt_id), None
        )
        if entry is None:
            raise AttemptJournalError("unknown_attempt", False)
        if entry["verification"] is not None:
            raise AttemptJournalError("duplicate_verification", False)
        observation = entry["observation"]
        if (
            observation is None
            or cast(dict[str, object], observation)["status"] != "observed"
        ):
            raise AttemptJournalError("attempt_not_observed", False)
        if (
            report.repository_root != self.root
            or entry["after_head"] is None
            or report.target_revision != entry["after_head"]
        ):
            raise AttemptJournalError("verification_identity_mismatch", False)
        try:
            event: dict[str, object] = {
                "event": "attempt_verification",
                "schema_version": 1,
                "attempt_id": attempt_id,
                "repository_root": str(self.root),
                "repository_id": entry["repository_id"],
                "contract_sha256": entry["contract_sha256"],
                "context_sha256": entry["context_sha256"],
                "before_head": entry["before_head"],
                "target_revision": report.target_revision,
                "technical_kind": report.technical_decision.kind.value,
                "overall_kind": report.overall_kind.value,
                "commands": [
                    _command_result(result) for result in report.profile.results
                ],
                "baseline_commands": [
                    _command_result(result)
                    for result in report.profile.baseline.results
                ],
                "reason_command_sha256": [
                    _command_digest(command)
                    for command in report.technical_decision.reason_commands
                ],
                "protected_inputs": [
                    {
                        "path_sha256": hashlib.sha256(
                            check.path.encode("utf-8")
                        ).hexdigest(),
                        "state": check.state.value,
                    }
                    for check in report.protected_input_checks
                ],
                "output_identifiers": _output_identifiers(entry["outputs"]),
            }
            _verification(event, entry)
        except (TypeError, ValueError, AttributeError, UnicodeError):
            raise AttemptJournalError("invalid_verification", False) from None
        self._validate_persistence()
        try:
            _append(self.journal, self.root, event)
        except Exception:
            raise AttemptJournalError("verification_append_failed", False) from None
        if refresh:
            self._refresh(journal_recorded=True)
        return event

    def _validate_persistence(self) -> None:
        try:
            _require(
                isinstance(self.journal, EventJournal)
                and isinstance(self.state_store, CurrentStateStore)
            )
            _validate_journal(self.journal, self.root)
            path = self.state_store.path
            _require(path.parent == self.journal.path.parent)
            _require(
                path.is_absolute()
                and str(path) == str(path.resolve())
                and not path.is_relative_to(self.root)
            )
            _require(not path.is_symlink())
            if path.exists():
                _require(stat.S_ISREG(path.lstat().st_mode))
        except (OSError, ValueError, TypeError, RuntimeError):
            raise AttemptJournalError("unsafe_persistence", False) from None

    def refresh(self) -> dict[str, object]:
        """Explicitly rederive current view; no automatic startup reconciliation."""
        self._validate_persistence()
        return self._refresh(journal_recorded=False)

    def _refresh(self, *, journal_recorded: bool) -> dict[str, object]:
        try:
            attempts = self.read()
            raw = self.journal.path.read_bytes() if self.journal.path.exists() else b""
            summary: dict[str, object] = {
                "schema_version": 1,
                "attempt_count": len(attempts),
                "journal": {
                    "path": str(self.journal.path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                "active_attempt": attempts[-1] if attempts else None,
            }
            state = self.state_store.read() or {}
            state["attempt_summary"] = summary
            self._validate_persistence()
            self.state_store.write(state)
        except Exception:
            raise AttemptJournalError(
                "summary_write_failed", journal_recorded
            ) from None
        return summary


class AttemptJournalError(RuntimeError):
    """Fixed failure classification and whether this operation appended a record."""

    def __init__(self, code: str, journal_recorded: bool) -> None:
        super().__init__(code)
        self.code = code
        self.journal_recorded = journal_recorded


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid attempt metadata")


def _mapping(value: object) -> dict[str, object]:
    _require(isinstance(value, dict))
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    _require(
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\0" not in value
    )
    return cast(str, value)


def _boolean(value: object) -> bool:
    _require(type(value) is bool)
    return cast(bool, value)


def _nonnegative(value: object) -> int:
    _require(type(value) is int and value >= 0)
    return cast(int, value)


def _paths(value: object) -> tuple[str, ...]:
    _require(isinstance(value, list))
    values = cast(list[object], value)
    _require(all(_valid_path(item) for item in values))
    result = tuple(cast(str, item) for item in values)
    _require(len(result) == len(set(result)))
    return result


def _observation(value: object) -> RepositoryObservation:
    data = _mapping(value)
    _require(isinstance(data["files"], list) and isinstance(data["status"], list))
    files: list[FileFingerprint] = []
    for item in cast(list[object], data["files"]):
        entry = _mapping(item)
        files.append(
            FileFingerprint(
                cast(str, entry["path"]),
                cast(Literal["regular", "symlink", "missing"], entry["kind"]),
                cast(str | None, entry["sha256"]),
                cast(int | None, entry["size_bytes"]),
                cast(bool | None, entry["executable"]),
                cast(int | None, entry.get("mode")),
            )
        )
    statuses: list[GitStatusEntry] = []
    for item in cast(list[object], data["status"]):
        entry = _mapping(item)
        statuses.append(
            GitStatusEntry(cast(str, entry["path"]), cast(str, entry["status"]))
        )
    snapshot = RepositoryObservation(
        cast(str, data["repository_root"]),
        cast(str, data["repository_id"]),
        tuple(files),
        cast(str | None, data["head"]),
        cast(str, data["index_sha256"]),
        tuple(statuses),
        cast(str | None, data.get("head_ref")),
    )
    _validate_observation(snapshot)
    return snapshot


def _delta(value: object) -> ObservationDelta:
    data = _mapping(value)
    return ObservationDelta(
        _paths(data["added"]),
        _paths(data["modified"]),
        _paths(data["deleted"]),
        _boolean(data["head_changed"]),
        _boolean(data["index_changed"]),
        _boolean(data["status_changed"]),
    )


def _capture(value: object) -> dict[str, object]:
    data = _mapping(value)
    return {
        "retained_bytes": _nonnegative(data["retained_bytes"]),
        "truncated": _boolean(data["truncated"]),
    }


def _process(value: object) -> dict[str, object]:
    data = _mapping(value)
    status = data["process_status"]
    exit_code = data["exit_code"]
    _require(status in {"successful_exit", "nonzero_exit", "timeout", "launch_error"})
    _require(exit_code is None or type(exit_code) is int)
    _require(
        (status == "successful_exit" and exit_code == 0)
        or (status == "nonzero_exit" and exit_code is not None and exit_code != 0)
        or status == "timeout"
        or (status == "launch_error" and exit_code is None)
    )
    duration = data["duration_seconds"]
    _require(
        type(duration) in {int, float}
        and math.isfinite(cast(float, duration))
        and cast(float, duration) >= 0
    )
    final_status = data["final_output_status"]
    _require(final_status in {"available", "missing", "unsafe", "unreadable"})
    _require((final_status == "available") == (data["final_message"] is not None))
    return {
        "process_status": status,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "stdout": _capture(data["stdout"]),
        "stderr": _capture(data["stderr"]),
        "final_message": None
        if data["final_message"] is None
        else _capture(data["final_message"]),
        "final_output_status": final_status,
    }


def _output(value: object) -> dict[str, object]:
    data = _mapping(value)
    _require(_digest(data["identifier"]))
    return {
        "identifier": data["identifier"],
        "byte_count": _nonnegative(data["byte_count"]),
        "truncated": _boolean(data["truncated"]),
        "captured_bytes": _nonnegative(data["captured_bytes"]),
        "capture_truncated": _boolean(data["capture_truncated"]),
    }


def _command_digest(command: tuple[str, ...]) -> str:
    _require(
        isinstance(command, tuple)
        and bool(command)
        and all(isinstance(part, str) for part in command)
    )
    return hashlib.sha256(
        json.dumps(command, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _command_result(result: CommandResult) -> dict[str, object]:
    _require(isinstance(result, CommandResult))
    _require(result.exit_code is None or type(result.exit_code) is int)
    return {
        "command_sha256": _command_digest(result.command),
        "exit_code": result.exit_code,
        "timed_out": _boolean(result.timed_out),
    }


def _command_metadata(value: object) -> list[dict[str, object]]:
    _require(isinstance(value, list))
    result: list[dict[str, object]] = []
    for item in cast(list[object], value):
        data = _mapping(item)
        _require(_digest(data["command_sha256"]))
        _require(data["exit_code"] is None or type(data["exit_code"]) is int)
        result.append(
            {
                "command_sha256": data["command_sha256"],
                "exit_code": data["exit_code"],
                "timed_out": _boolean(data["timed_out"]),
            }
        )
    return result


def _output_identifiers(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    data = _mapping(value)
    return {
        channel: None
        if data[channel] is None
        else _mapping(data[channel])["identifier"]
        for channel in ("stdout", "stderr", "final_message")
    }


def _verification(
    event: dict[str, object], entry: dict[str, object]
) -> dict[str, object]:
    _require(type(event["schema_version"]) is int and event["schema_version"] == 1)
    for key in (
        "attempt_id",
        "repository_root",
        "repository_id",
        "contract_sha256",
        "context_sha256",
        "before_head",
    ):
        _require(event[key] == entry[key])
    _require(
        entry["after_head"] is not None
        and event["target_revision"] == entry["after_head"]
    )
    _require(
        event["technical_kind"] in {"pass", "fail", "blocked"}
        and event["overall_kind"] in {"pass", "fail", "blocked"}
    )
    protected: list[dict[str, object]] = []
    _require(isinstance(event["protected_inputs"], list))
    for item in cast(list[object], event["protected_inputs"]):
        check = _mapping(item)
        _require(
            _digest(check["path_sha256"])
            and check["state"] in {"unchanged", "modified", "missing", "unverifiable"}
        )
        protected.append({"path_sha256": check["path_sha256"], "state": check["state"]})
    _require(
        event["overall_kind"]
        == (
            "fail"
            if any(check["state"] != "unchanged" for check in protected)
            else event["technical_kind"]
        )
    )
    reasons = event["reason_command_sha256"]
    _require(
        isinstance(reasons, list)
        and all(_digest(item) for item in cast(list[object], reasons))
    )
    _require(event["output_identifiers"] == _output_identifiers(entry["outputs"]))
    return {
        **{
            key: event[key]
            for key in (
                "event",
                "schema_version",
                "attempt_id",
                "repository_root",
                "repository_id",
                "contract_sha256",
                "context_sha256",
                "before_head",
                "target_revision",
                "technical_kind",
                "overall_kind",
            )
        },
        "commands": _command_metadata(event["commands"]),
        "baseline_commands": _command_metadata(event["baseline_commands"]),
        "reason_command_sha256": list(cast(list[object], reasons)),
        "protected_inputs": protected,
        "output_identifiers": _output_identifiers(entry["outputs"]),
    }

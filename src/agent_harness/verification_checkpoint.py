"""Safe checkpoint facts; these alone never authorize continuation.

Reconstructed results intentionally have empty stdout/stderr: historical output
is unavailable. Callers must validate the durable journal, original admission,
protected fingerprints and live observation before using these facts.
"""

from __future__ import annotations

import math
from typing import cast

from agent_harness.attempt import (
    _command_digest,
    _command_metadata,
    _command_result,
    _mapping,
    _observation,
    _process,
)
from agent_harness.integrity import (
    ProtectedDirectoryFingerprint,
    ProtectedInputFingerprint,
)
from agent_harness.runner import CommandResult
from agent_harness.scope import _digest


def _seconds(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid_verification_checkpoint")
    result = float(cast(int | float, value))
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid_verification_checkpoint")
    return result


def safe_results(results: tuple[CommandResult, ...]) -> list[dict[str, object]]:
    """Serialize only digests and safe returned process facts, in policy order."""
    try:
        encoded = []
        for result in results:
            facts = _command_result(result)
            _completed_process(facts)
            encoded.append(
                {
                    **facts,
                    "duration_seconds": _seconds(result.duration_seconds),
                }
            )
        return encoded
    except Exception:
        raise ValueError("invalid_verification_checkpoint") from None


def restore_results(
    value: object, commands: tuple[tuple[str, ...], ...]
) -> tuple[CommandResult, ...]:
    """Restore positional metadata against supplied admitted commands."""
    try:
        metadata = _command_metadata(value)
        if len(metadata) != len(commands):
            raise ValueError
        restored = []
        for raw, facts, command in zip(
            cast(list[dict[str, object]], value), metadata, commands, strict=True
        ):
            if set(raw) != {
                "command_sha256",
                "exit_code",
                "timed_out",
                "duration_seconds",
            } or facts["command_sha256"] != _command_digest(command):
                raise ValueError
            _completed_process(facts)
            restored.append(
                CommandResult(
                    command,
                    cast(int | None, facts["exit_code"]),
                    "",
                    "",
                    _seconds(raw["duration_seconds"]),
                    cast(bool, facts["timed_out"]),
                )
            )
        return tuple(restored)
    except Exception:
        raise ValueError("invalid_verification_checkpoint") from None


def _completed_process(facts: dict[str, object]) -> None:
    if (facts["exit_code"] is None) != facts["timed_out"]:
        raise ValueError("invalid_verification_checkpoint")


def carried_elapsed(saved: object, checkpoint_wall: object, now_wall: object) -> float:
    """Charge offline downtime under the admitted trusted-wall-clock assumption.

    Each resumed invocation adds its own monotonic elapsed to this returned
    charge. Never subtract a previous process's monotonic timestamp.
    """
    try:
        elapsed = _seconds(saved)
        checkpoint = _seconds(checkpoint_wall)
        now = _seconds(now_wall)
        if now < checkpoint:
            raise ValueError
        return _seconds(elapsed + (now - checkpoint))
    except Exception:
        raise ValueError("invalid_verification_checkpoint") from None


def validate_boundaries(events: list[dict[str, object]]) -> None:
    """Reject orphan, duplicated, out-of-order and post-terminal phase markers."""
    pending: str | None = None
    intent: str | None = None
    first: dict[str, object] | None = None
    stored: object = None
    observed: dict[str, object] | None = None
    scope: dict[str, object] | None = None
    native_count = 0
    loop_count = 0
    terminal = False
    for event in events:
        kind = event.get("event")
        if kind == "loop_start":
            loop_count += 1
        if kind == "turn_intent":
            native_count += 1
        if kind == "turn_observation":
            observed = event
        if kind == "scope_decision":
            scope = event
        if kind == "native_output_stored":
            stored = event.get("attempt_id")
        if kind == "attempt_verification" and first is not None:
            if (
                event.get("attempt_id") != stored
                or intent is None
                or intent != pending
                or (event.get("overall_kind") == "pass" and intent != "final")
            ):
                raise ValueError("invalid_verification_checkpoint")
        if kind in ("loop_outcome", "attempt_verification"):
            terminal = True
        if kind not in ("verification_checkpoint", "verification_phase_intent"):
            continue
        if terminal or not stored or event.get("attempt_id") != stored:
            raise ValueError("invalid_verification_checkpoint")
        if native_count != 1 or loop_count != 1 or observed is None or scope is None:
            raise ValueError("invalid_verification_checkpoint")
        process = _process(observed.get("process"))
        if (
            process["process_status"] != "successful_exit"
            or process["exit_code"] != 0
            or process["final_output_status"] != "available"
            or scope.get("proceed") is not True
            or observed.get("attempt_id") != stored
            or scope.get("attempt_id") != stored
        ):
            raise ValueError("invalid_verification_checkpoint")
        if kind == "verification_phase_intent":
            if set(event) != {"event", "attempt_id", "phase"}:
                raise ValueError("invalid_verification_checkpoint")
            if intent is not None or pending is None or event.get("phase") != pending:
                raise ValueError("invalid_verification_checkpoint")
            intent = pending
            continue
        if set(event) != {
            "event",
            "version",
            "attempt_id",
            "pending_phase",
            "binding_sha256",
            "baseline",
            "completed_results",
            "protected_files",
            "protected_directories",
            "observation",
            "commands",
            "elapsed_seconds",
            "wall_seconds",
        }:
            raise ValueError("invalid_verification_checkpoint")
        if not _digest(event.get("binding_sha256")) or _observation(
            event.get("observation")
        ) != _observation(observed.get("after")):
            raise ValueError("invalid_verification_checkpoint")
        _protected_metadata(event)
        phase = event.get("pending_phase")
        if type(event.get("version")) is not int or event["version"] != 1:
            raise ValueError("invalid_verification_checkpoint")
        _seconds(event.get("elapsed_seconds"))
        _seconds(event.get("wall_seconds"))
        baseline = _strict_results(event.get("baseline"))
        count = len(baseline)
        expected = 1 + count * (2 if phase == "final" else 1)
        if (
            not count
            or type(event.get("commands")) is not int
            or event["commands"] != expected
        ):
            raise ValueError("invalid_verification_checkpoint")
        if first is None:
            if phase != "development" or intent is not None:
                raise ValueError("invalid_verification_checkpoint")
            if event.get("completed_results") != []:
                raise ValueError("invalid_verification_checkpoint")
            first = event
        else:
            if phase != "final" or intent != "development":
                raise ValueError("invalid_verification_checkpoint")
            for key in (
                "binding_sha256",
                "baseline",
                "protected_files",
                "protected_directories",
                "observation",
                "attempt_id",
            ):
                if event.get(key) != first.get(key):
                    raise ValueError("invalid_verification_checkpoint")
            if _seconds(event["elapsed_seconds"]) < _seconds(
                first["elapsed_seconds"]
            ) or _seconds(event["wall_seconds"]) < _seconds(first["wall_seconds"]):
                raise ValueError("invalid_verification_checkpoint")
            completed = _strict_results(event.get("completed_results"))
            if [r["command_sha256"] for r in completed] != [
                r["command_sha256"] for r in baseline
            ]:
                raise ValueError("invalid_verification_checkpoint")
            if len(completed) != count or any(
                row["exit_code"] != 0 or row["timed_out"] for row in completed
            ):
                raise ValueError("invalid_verification_checkpoint")
        pending = phase
        intent = None


def _strict_results(value: object) -> list[dict[str, object]]:
    rows = _command_metadata(value)
    for raw, facts in zip(cast(list[object], value), rows, strict=True):
        item = _mapping(raw)
        if set(item) != {
            "command_sha256",
            "exit_code",
            "timed_out",
            "duration_seconds",
        }:
            raise ValueError("invalid_verification_checkpoint")
        _seconds(item["duration_seconds"])
        _completed_process(facts)
    return rows


def _protected_metadata(event: dict[str, object]) -> None:
    files, directories = event["protected_files"], event["protected_directories"]
    if type(files) is not list or type(directories) is not list:
        raise ValueError("invalid_verification_checkpoint")
    paths = []
    for raw in files:
        row = _mapping(raw)
        if set(row) not in ({"path", "sha256"}, {"path", "sha256", "mode"}):
            raise ValueError("invalid_verification_checkpoint")
        item = ProtectedInputFingerprint(
            cast(str, row["path"]),
            cast(str, row["sha256"]),
            cast(int | None, row.get("mode")),
        )
        paths.append(item.path)
    for raw in directories:
        row = _mapping(raw)
        if set(row) != {"path", "files"} or type(row["files"]) is not list:
            raise ValueError("invalid_verification_checkpoint")
        members = []
        for value in cast(list[object], row["files"]):
            member = _mapping(value)
            if set(member) not in ({"path", "sha256"}, {"path", "sha256", "mode"}):
                raise ValueError("invalid_verification_checkpoint")
            members.append(
                ProtectedInputFingerprint(
                    cast(str, member["path"]),
                    cast(str, member["sha256"]),
                    cast(int | None, member.get("mode")),
                )
            )
        directory = ProtectedDirectoryFingerprint(
            cast(str, row["path"]), tuple(members)
        )
        paths.append(directory.path)
    if len(paths) != len(set(paths)):
        raise ValueError("invalid_verification_checkpoint")

"""Finite, read-only historical projections; no workspace or authenticity proof."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import cast

_CURRENT_CAP = 1024 * 1024
_JOURNAL_CAP = 8 * 1024 * 1024
_STATUSES = {"pass", "fail", "blocked", "stopped", "human", "unknown"}
_IDENTITY = (
    "attempt_id",
    "repository_root",
    "repository_id",
    "contract_sha256",
    "context_sha256",
    "before_head",
)


class _Invalid(ValueError):
    pass


def _require(condition: bool, code: str = "inconsistent_history") -> None:
    if not condition:
        raise _Invalid(code)


def _mapping(value: object) -> dict[str, object]:
    _require(isinstance(value, dict), "invalid_record")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    _require(
        isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value,
        "invalid_record",
    )
    return cast(str, value)


def _digest(value: object, lengths: tuple[int, ...] = (64,)) -> str:
    text = _text(value)
    _require(
        len(text) in lengths and all(c in "0123456789abcdef" for c in text),
        "invalid_record",
    )
    return text


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "invalid_record")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise _Invalid("invalid_record")


def _decode(raw: bytes) -> dict[str, object]:
    try:
        return _mapping(
            json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError, ValueError):
        raise _Invalid("invalid_record") from None


def _signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_pair(directory: Path | str) -> tuple[Path, bytes, bytes]:
    # Walk by directory descriptors so neither leaf nor ancestor symlinks resolve.
    path = Path(directory)
    _require(".." not in path.parts, "unsafe_input")
    path = Path(os.path.abspath(path))
    descriptors: list[int] = []
    bindings: list[tuple[int, str, tuple[int, ...]]] = []
    leaves: list[tuple[int, str, int, tuple[int, ...]]] = []
    try:
        parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for part in path.parts[1:]:
            descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            descriptors.append(descriptor)
            signature = _signature(os.fstat(descriptor))
            bindings.append((parent, part, signature))
            parent = descriptor
        for name, cap in (
            ("current.json", _CURRENT_CAP),
            ("events.jsonl", _JOURNAL_CAP),
        ):
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                "unsafe_input",
            )
            _require(metadata.st_size <= cap, "input_limit")
            leaves.append((descriptor, name, cap, _signature(metadata)))
        contents: list[bytes] = []
        for descriptor, _, cap, signature in leaves:
            chunks: list[bytes] = []
            remaining = cap + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            _require(len(payload) <= cap, "input_limit")
            _require(
                len(payload) == signature[4]
                and _signature(os.fstat(descriptor)) == signature,
                "changed_input",
            )
            contents.append(payload)
        for descriptor, name, _, signature in leaves:
            _require(
                _signature(os.fstat(descriptor)) == signature
                and _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                == signature,
                "changed_input",
            )
        for ancestor, name, signature in bindings:
            # Directory atime/mtime can change independently: identity and type suffice.
            _require(
                _signature(os.stat(name, dir_fd=ancestor, follow_symlinks=False))[:3]
                == signature[:3],
                "changed_input",
            )
        return path, contents[0], contents[1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _outputs(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    source = _mapping(value)
    result: dict[str, object] = {}
    for channel in ("stdout", "stderr", "final_message"):
        item = source[channel]
        if item is None:
            result[channel] = None
            continue
        data = _mapping(item)
        for key in ("byte_count", "captured_bytes"):
            _require(type(data[key]) is int and cast(int, data[key]) >= 0)
        for key in ("truncated", "capture_truncated"):
            _require(type(data[key]) is bool)
        result[channel] = {
            key: data[key]
            for key in (
                "identifier",
                "byte_count",
                "truncated",
                "captured_bytes",
                "capture_truncated",
            )
        }
        _digest(data["identifier"])
    return result


def _verification(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    data = _mapping(value)
    _require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    _require(data["event"] == "attempt_verification")
    for key in _IDENTITY:
        _text(data[key])
    _digest(data["target_revision"], (40, 64))
    for key in ("technical_kind", "overall_kind"):
        _require(
            isinstance(data[key], str) and data[key] in {"pass", "fail", "blocked"}
        )
    for key in ("commands", "baseline_commands"):
        commands = data[key]
        _require(isinstance(commands, list))
        for command in cast(list[object], commands):
            row = _mapping(command)
            _digest(row["command_sha256"])
            _require(row["exit_code"] is None or type(row["exit_code"]) is int)
            _require(type(row["timed_out"]) is bool)
    reasons = data["reason_command_sha256"]
    _require(isinstance(reasons, list))
    for reason in cast(list[object], reasons):
        _digest(reason)
    protected = data["protected_inputs"]
    _require(isinstance(protected, list))
    protected_changed = False
    for item in cast(list[object], protected):
        check = _mapping(item)
        _digest(check["path_sha256"])
        _require(
            isinstance(check["state"], str)
            and check["state"] in {"unchanged", "modified", "missing", "unverifiable"}
        )
        protected_changed = protected_changed or check["state"] != "unchanged"
    _require(
        data["overall_kind"]
        == ("fail" if protected_changed else data["technical_kind"])
    )
    return {
        key: data[key]
        for key in (
            *_IDENTITY,
            "event",
            "schema_version",
            "target_revision",
            "technical_kind",
            "overall_kind",
            "commands",
            "baseline_commands",
            "reason_command_sha256",
            "protected_inputs",
            "output_identifiers",
        )
    }


def _project(path: Path, current_raw: bytes, journal_raw: bytes) -> dict[str, object]:
    current = _decode(current_raw)
    _require(journal_raw.endswith(b"\n"), "incomplete_history")
    lines = journal_raw.splitlines(keepends=True)
    _require(len(lines) <= 10000, "input_limit")
    events = [_decode(line) for line in lines]
    _require(len(events) >= 2, "incomplete_history")
    binding: dict[str, object] | None = None
    if events[0].get("event") == "run_binding":
        binding = events[0]
        _require(
            set(binding)
            == {"event", "schema_version", "initial_head", "config_sha256", "backend"}
        )
        _require(
            type(binding["schema_version"]) is int
            and binding["schema_version"] in (1, 2)
        )
        _digest(binding["initial_head"], (40, 64))
        _digest(binding["config_sha256"])
        _require(binding["backend"] in ("codex", "claude"))
        events = events[1:]
    _require(len(events) >= 2, "incomplete_history")
    _require(not any(event.get("event") == "run_binding" for event in events))
    _require(events[0].get("event") == "loop_start")
    terminal = events[-1]
    _require(terminal.get("event") == "loop_outcome", "incomplete_history")
    _require(sum(event.get("event") == "loop_start" for event in events) == 1)
    _require(sum(event.get("event") == "loop_outcome" for event in events) == 1)
    _require(
        json.dumps(current.get("loop_summary"), sort_keys=True)
        == json.dumps(terminal, sort_keys=True)
    )
    outcome = {
        key: terminal[key]
        for key in (
            "event",
            "status",
            "reason",
            "commands",
            "elapsed_seconds",
            "elapsed_boundary",
            "attempt_ids",
        )
    }
    _require(isinstance(outcome["status"], str) and outcome["status"] in _STATUSES)
    _text(outcome["reason"])
    _require(type(outcome["commands"]) is int and outcome["commands"] >= 0)
    seconds = outcome["elapsed_seconds"]
    _require(
        type(seconds) in (int, float)
        and math.isfinite(cast(float, seconds))
        and cast(float, seconds) >= 0
    )
    _require(outcome["elapsed_boundary"] == "before_outcome_persistence")
    summary = _mapping(current["attempt_summary"])
    _require(type(summary["schema_version"]) is int and summary["schema_version"] == 1)
    journal = _mapping(summary["journal"])
    _require(journal["path"] == str(path / "events.jsonl"))
    digest = _digest(journal["sha256"])
    _require(
        digest
        in {
            hashlib.sha256(journal_raw).hexdigest(),
            hashlib.sha256(b"".join(lines[:-1])).hexdigest(),
        }
    )
    intents = [event for event in events if event.get("event") == "turn_intent"]
    ids = [_text(event["attempt_id"]) for event in intents]
    _require(len(ids) == len(set(ids)) and outcome["attempt_ids"] == ids)
    _require(
        type(summary["attempt_count"]) is int and summary["attempt_count"] == len(ids)
    )
    for intent in intents:
        if binding is not None:
            _require(_mapping(intent["before"])["head"] == binding["initial_head"])
        _require(intent["repository_id"] == events[0]["repository_id"])
        before = _mapping(intent["before"])
        _require(
            before["repository_id"] == intent["repository_id"]
            and before["repository_root"] == intent["repository_root"]
        )
    for event in events:
        if "attempt_id" in event:
            if (
                not ids
                and event.get("event") == "loop_protected_inputs"
                and event["attempt_id"] is None
            ):
                continue
            _require(event["attempt_id"] in ids)
    active = summary["active_attempt"]
    verification: dict[str, object] | None = None
    outputs: dict[str, object] | None = None
    revision: object = None
    if not ids:
        _require(active is None and outcome["status"] != "pass")
    else:
        entry = _mapping(active)
        intent = intents[-1]
        for key in _IDENTITY:
            _require(
                entry[key] == intent[key]
                if key != "before_head"
                else entry[key] == _mapping(intent["before"])["head"]
            )
            _text(entry[key])
        _require(intent["repository_id"] == events[0]["repository_id"])
        _require(_mapping(intent["before"])["repository_id"] == intent["repository_id"])
        for key in ("contract_sha256", "context_sha256"):
            _digest(entry[key])
        relevant = [event for event in events if event.get("attempt_id") == ids[-1]]
        observations = [
            event
            for event in relevant
            if event.get("event")
            in {
                "turn_observation",
                "callback_observation",
                "turn_observation_failed",
                "callback_observation_failed",
            }
        ]
        _require(bool(observations), "incomplete_history")
        observed = observations[-1]
        revision = entry["after_head"]
        if observed["event"] in {"turn_observation", "callback_observation"}:
            after = _mapping(observed["after"])
            _require(
                after["repository_id"] == entry["repository_id"]
                and after["repository_root"] == entry["repository_root"]
            )
            _require(revision == after["head"])
            if revision is not None:
                _digest(revision, (40, 64))
            _require(
                entry["observation"]
                == {"status": "observed", "process": observed["process"]}
            )
        else:
            _require(
                revision is None
                and _mapping(entry["observation"])["status"] == "failed"
            )
        stored = [
            event for event in relevant if event.get("event") == "native_output_stored"
        ]
        outputs = _outputs(entry["outputs"])
        _require(len(stored) <= 1)
        if stored:
            _require(stored[0]["repository_id"] == entry["repository_id"])
            _require(outputs == _outputs(stored[0]))
        else:
            _require(outputs is None)
        verified = [
            event for event in relevant if event.get("event") == "attempt_verification"
        ]
        _require(len(verified) <= 1)
        verification = _verification(entry["verification"])
        _require(verification == (_verification(verified[0]) if verified else None))
        if verification is not None:
            _require(all(verification[key] == entry[key] for key in _IDENTITY))
            _require(verification["target_revision"] == revision)
            _require(
                verification["output_identifiers"]
                == (
                    None
                    if outputs is None
                    else {
                        key: None if value is None else _mapping(value)["identifier"]
                        for key, value in outputs.items()
                    }
                )
            )
        if outcome["status"] == "pass":
            _require(
                verification is not None and verification["overall_kind"] == "pass"
            )
    if binding is not None and binding["schema_version"] == 2:
        _require("run_result" in current, "incomplete_history")
        wrapper = _mapping(current["run_result"])
        _require(set(wrapper) == {"status", "reason"})
        _require(type(wrapper["status"]) is str and wrapper["status"] in _STATUSES)
        _text(wrapper["reason"])
        _require(
            wrapper == {"status": outcome["status"], "reason": outcome["reason"]}
            or (
                wrapper["status"] == "unknown"
                and wrapper["reason"]
                in {"lock_release_failed", "execution_failed", "interrupted"}
            )
        )
        outcome.update(status=wrapper["status"], reason=wrapper["reason"])
    return {
        "schema_version": 1,
        "historical_only": True,
        "current_workspace_verified": False,
        "recorded_status": outcome["status"],
        "recorded_reason": outcome["reason"],
        "attempt_ids": ids,
        "target_revision": revision,
        "verification": None
        if verification is None
        else {
            "technical_kind": verification["technical_kind"],
            "overall_kind": verification["overall_kind"],
            "command_count": len(cast(list[object], verification["commands"])),
        },
        "evidence": {
            "current": {
                "path": str(path / "current.json"),
                "sha256": hashlib.sha256(current_raw).hexdigest(),
            },
            "journal": {
                "path": str(path / "events.jsonl"),
                "sha256": hashlib.sha256(journal_raw).hexdigest(),
            },
            "outputs": None
            if outputs is None
            else {
                key: None if value is None else _mapping(value)["identifier"]
                for key, value in outputs.items()
            },
        },
        "diagnostics": [],
    }


def summarize_execution(state_directory: Path | str) -> dict[str, object]:
    """Return only historical metadata; any unreadable/inconsistent view is UNKNOWN.

    This checks supported terminal bindings, not every event's runtime semantics,
    authenticity, current workspace, referenced output bytes or merge authority.
    """
    try:
        path, current, journal = _read_pair(state_directory)
        return _project(path, current, journal)
    except _Invalid as error:
        diagnostic = str(error)
    except FileNotFoundError:
        diagnostic = "missing_input"
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        diagnostic = "unreadable_or_invalid_history"
    return {
        "schema_version": 1,
        "historical_only": True,
        "current_workspace_verified": False,
        "recorded_status": "unknown",
        "recorded_reason": None,
        "attempt_ids": [],
        "target_revision": None,
        "verification": None,
        "evidence": None,
        "diagnostics": [diagnostic],
    }

"""Explicit trusted-caller check collection; stored hashes are not authentication.

Only fixed receipt siblings are read. Raw checker XML stays checker-owned and may
contain private data; normalized receipts never retain testcase text or output.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_harness.policy import VerificationPolicy
from agent_harness.repository import (
    _is_within_directory,
    resolve_repository_root,
    validate_runtime_state_directory,
)
from agent_harness.review_evidence import _candidate, candidate_sha256
from agent_harness.runner import (
    ControlledCommandRunner,
    VerificationExecutables,
    bind_verification_executables,
)
from agent_harness.turn import RepositoryObservation

_LIMIT = 4 * 1024 * 1024
_COUNTS = ("tests", "failures", "errors", "skipped")


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid external check text")
    return value


def _digest(value: object) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("invalid external digest")


def _read(path: Path) -> bytes:
    for ancestor in (*path.parents, path):
        if ancestor.is_symlink():
            raise ValueError("external check symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > _LIMIT
        ):
            raise ValueError("unsafe or oversized external check file")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, _LIMIT + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > _LIMIT:
                raise ValueError("external check input limit")
        after = os.fstat(fd)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("external check changed during read")
        return bytes(raw)
    finally:
        os.close(fd)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in items:
        if key in data:
            raise ValueError("duplicate external check key")
        data[key] = value
    return data


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError("invalid external check record")
    return value


def _write(path: Path, value: object) -> None:
    for ancestor in path.parents:
        if ancestor.is_symlink():
            raise ValueError("external check write symlink")
    raw = _bytes(value)
    if len(raw) > _LIMIT:
        raise ValueError("external check record limit")
    # Exclusive files preserve an existing intent/result on every failure path.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()


def _junit(raw: bytes) -> dict[str, object]:
    """Strict pytest/Vitest JUnit subset; count actual cases and cross-check totals."""
    # JUnit here has no need for declarations, entities, namespaces or extensions
    # that affect counts. Reject rather than resolve potentially private inputs.
    if b"<!" in raw or b"\x00" in raw:
        raise ValueError("unsupported JUnit declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("invalid JUnit XML") from error
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("invalid JUnit root")
    seen: set[tuple[str, str]] = set()

    def visit(node: ET.Element) -> dict[str, int]:
        totals = dict.fromkeys(_COUNTS, 0)
        for child in node:
            if child.tag in {"testsuite", "testsuites"}:
                counts = visit(child)
            elif child.tag == "testcase":
                if node.tag != "testsuite":
                    raise ValueError("testcase requires testsuite")
                for item in child:
                    if item.tag not in {
                        "failure",
                        "error",
                        "skipped",
                        "properties",
                        "system-out",
                        "system-err",
                    }:
                        raise ValueError("unsupported JUnit testcase child")
                    if item.tag == "properties":
                        if any(prop.tag != "property" or len(prop) for prop in item):
                            raise ValueError("unsupported JUnit properties")
                    elif len(item):
                        raise ValueError("unsupported nested JUnit content")
                identity = (child.get("classname", ""), child.get("name", ""))
                if not identity[1] or identity in seen:
                    raise ValueError("duplicate or unnamed JUnit testcase")
                seen.add(identity)
                outcomes = [
                    item.tag
                    for item in child
                    if item.tag in {"failure", "error", "skipped"}
                ]
                if len(outcomes) > 1:
                    raise ValueError("contradictory JUnit testcase")
                counts = dict(tests=1, failures=0, errors=0, skipped=0)
                if outcomes:
                    counts[
                        {
                            "failure": "failures",
                            "error": "errors",
                            "skipped": "skipped",
                        }[outcomes[0]]
                    ] = 1
            elif child.tag in {"properties", "system-out", "system-err"}:
                if child.tag == "properties":
                    if any(prop.tag != "property" or len(prop) for prop in child):
                        raise ValueError("unsupported JUnit properties")
                elif len(child):
                    raise ValueError("unsupported nested JUnit output")
                continue
            else:
                raise ValueError("unsupported JUnit element")
            for key in _COUNTS:
                totals[key] += counts[key]
        for key in _COUNTS:
            if key in node.attrib:
                text = node.attrib[key]
                if not text.isascii() or not text.isdigit() or int(text) != totals[key]:
                    raise ValueError("contradictory JUnit counts")
        return totals

    counts = visit(root)
    state = (
        "FAIL"
        if counts["failures"] or counts["errors"]
        else ("SKIP" if counts["skipped"] else "PASS" if counts["tests"] else "UNKNOWN")
    )
    return {"state": state, "counts": counts}


def _artifact(bundle: Path) -> dict[str, object]:
    try:
        raw = _read(bundle / "result.xml")
    except FileNotFoundError:
        return {
            "state": "UNKNOWN",
            "counts": None,
            "artifact": None,
            "reason": "missing_result",
        }
    digest = _hash(raw)
    try:
        parsed = _junit(raw)
    except (ValueError, RecursionError):
        return {
            "state": "UNKNOWN",
            "counts": None,
            "artifact": {"name": "result.xml", "sha256": digest},
            "reason": "invalid_result",
        }
    return {
        **parsed,
        "artifact": {"name": "result.xml", "sha256": digest},
        "reason": None,
    }


def _subject(root: Path) -> dict[str, object]:
    observation = RepositoryObservation.capture(root)
    return {
        "observation": asdict(observation),
        "candidate_sha256": candidate_sha256(observation),
    }


def _protected(paths: tuple[Path, ...]) -> list[dict[str, str]]:
    """Observe explicit protected entries including ignored files, without following links."""
    records = []
    for path in paths:
        entries: list[object] = []
        pending = [path]
        while pending:
            leaf = pending.pop()
            if len(entries) > 10000:
                raise ValueError("protected observation limit")
            try:
                info = leaf.lstat()
            except FileNotFoundError:
                entries.append((str(leaf), "missing"))
                continue
            if stat.S_ISDIR(info.st_mode):
                entries.append((str(leaf), "directory", stat.S_IMODE(info.st_mode)))
                pending.extend(sorted(leaf.iterdir()))
            elif stat.S_ISLNK(info.st_mode):
                entries.append(
                    (str(leaf), "symlink", _hash(os.fsencode(os.readlink(leaf))))
                )
            else:
                entries.append(
                    (str(leaf), "file", stat.S_IMODE(info.st_mode), _hash(_read(leaf)))
                )
        records.append({"path": str(path), "sha256": _hash(_bytes(entries))})
    return records


def collect_external_check(
    repository: Path | str,
    policy: VerificationPolicy,
    bundle: Path | str,
    *,
    actor: str,
    check_label: str,
    criterion: str,
    safe_command_display: str,
    protected_paths: tuple[Path, ...] = (),
    selections: VerificationExecutables | None = None,
) -> dict[str, Any]:
    """Run ONE already-authorized command. Caller supplies authority and safe display.

    The command must write fresh bundle/result.xml. No retries, shell, native
    inference, environment serialization, raw argv or output persistence occurs.
    Selection/observation are point-in-time checks, not an OS sandbox.
    """
    if len(policy.commands) != 1:
        raise ValueError("external collection requires one command")
    labels = {
        "actor": _text(actor),
        "check_label": _text(check_label),
        "criterion": _text(criterion),
        "caller_command_display": _text(safe_command_display),
    }
    root = resolve_repository_root(repository)
    destination = Path(os.path.abspath(bundle))
    protected = tuple(Path(os.path.abspath(path)) for path in protected_paths)
    validate_runtime_state_directory(destination, root)
    if any(
        _is_within_directory(destination.resolve(), path.resolve())
        or _is_within_directory(path.resolve(), destination.resolve())
        for path in protected
    ):
        raise ValueError("bundle overlaps protected path")
    selected = selections or bind_verification_executables(policy, root)
    if (
        len(selected.executables) != 1
        or selected.executables[0].command != policy.commands[0]
    ):
        raise ValueError("external selection does not match command")
    if selected.repository != str(root) or selected.working_directory != str(
        (root / policy.working_directory).resolve()
    ):
        raise ValueError("external selection does not match target")
    before = _subject(root)
    protected_before = _protected(protected)
    destination.mkdir(mode=0o700)  # Parent must exist; fresh exclusive bundle only.
    entry = selected.executables[0]
    intent = {
        "schema_version": 1,
        "origin": "external_command_receipt",
        **labels,
        "working_directory": selected.working_directory,
        "command_sha256": _hash(
            _bytes((entry.selected or policy.commands[0][0], *policy.commands[0][1:]))
        ),
        "executable": {
            "selected": entry.selected,
            "resolved": entry.resolved,
            "sha256": entry.sha256,
        },
        "timeout_seconds": policy.timeout_seconds,
        "before": before,
        "protected_before": protected_before,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _write(destination / "intent.json", intent)
    started = datetime.now(UTC).isoformat()
    tick = time.monotonic()
    result = ControlledCommandRunner(max_output_bytes=65536).run(
        policy, root, selections=selected
    )[0]
    duration = time.monotonic() - tick
    ended = datetime.now(UTC).isoformat()
    process = {
        "state": "TIMEOUT"
        if result.timed_out
        else "LAUNCH_ERROR"
        if result.exit_code is None
        else "EXITED",
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    }
    _write(
        destination / "process.json",
        {
            "intent_sha256": _hash(_bytes(intent)),
            "started_at": started,
            "ended_at": ended,
            "duration_seconds": duration,
            "process": process,
        },
    )
    # Any exception below leaves the original intent intact, without completion.
    after = _subject(root)
    tests = _artifact(destination)
    protected_after = _protected(protected)
    subject_state = "unchanged" if before == after else "changed"
    protected_state = "unchanged" if protected_before == protected_after else "changed"
    outcome = (
        "TIMEOUT"
        if result.timed_out
        else "UNKNOWN"
        if result.exit_code is None
        else "FAIL"
        if result.exit_code != 0
        else tests["state"]
    )
    if not result.timed_out and (
        protected_state != "unchanged" or subject_state != "unchanged"
    ):
        outcome = "UNKNOWN"
    payload = {
        "intent_sha256": _hash(_bytes(intent)),
        "intent": intent,
        "after": after,
        "started_at": started,
        "ended_at": ended,
        "duration_seconds": duration,
        "process": process,
        "tests": tests,
        "subject_state": subject_state,
        "protected_after": protected_after,
        "protected_state": protected_state,
        "outcome": outcome,
    }
    _write(
        destination / "receipt.json",
        {"payload": payload, "sha256": _hash(_bytes(payload))},
    )
    return read_external_check(destination / "receipt.json")


def read_external_check(path: Path | str) -> dict[str, Any]:
    """Read fixed siblings, validate linkage; no execution, recovery or live target read."""
    receipt_path = Path(os.path.abspath(path))
    if receipt_path.name != "receipt.json":
        raise ValueError("external check requires receipt.json")
    raw = _read(receipt_path)
    envelope = _json(raw)
    if set(envelope) != {"payload", "sha256"} or envelope["sha256"] != _hash(
        _bytes(envelope["payload"])
    ):
        raise ValueError("external receipt digest mismatch")
    data = envelope["payload"]
    if type(data) is not dict or set(data) != {
        "intent_sha256",
        "intent",
        "after",
        "started_at",
        "ended_at",
        "duration_seconds",
        "process",
        "tests",
        "subject_state",
        "protected_after",
        "protected_state",
        "outcome",
    }:
        raise ValueError("invalid external receipt")
    intent = _json(_read(receipt_path.parent / "intent.json"))
    if data["intent"] != intent or data["intent_sha256"] != _hash(_bytes(intent)):
        raise ValueError("external intent mismatch")
    if (
        set(intent)
        != {
            "schema_version",
            "origin",
            "actor",
            "check_label",
            "criterion",
            "caller_command_display",
            "working_directory",
            "command_sha256",
            "executable",
            "timeout_seconds",
            "before",
            "protected_before",
            "created_at",
        }
        or type(intent["schema_version"]) is not int
        or intent["schema_version"] != 1
        or intent["origin"] != "external_command_receipt"
    ):
        raise ValueError("invalid external intent")
    for key in (
        "actor",
        "check_label",
        "criterion",
        "caller_command_display",
        "working_directory",
    ):
        _text(intent[key])
    _digest(intent["command_sha256"])
    executable = intent["executable"]
    if type(executable) is not dict or set(executable) != {
        "selected",
        "resolved",
        "sha256",
    }:
        raise ValueError("invalid external executable")
    for key in ("selected", "resolved"):
        if (
            executable[key] is not None
            and not Path(_text(executable[key])).is_absolute()
        ):
            raise ValueError("invalid external executable path")
    if executable["sha256"] is not None:
        _digest(executable["sha256"])
    if executable["sha256"] is not None and (
        executable["selected"] is None or executable["resolved"] is None
    ):
        raise ValueError("invalid external executable proof")
    if type(intent["timeout_seconds"]) is not int or intent["timeout_seconds"] <= 0:
        raise ValueError("invalid external timeout")
    for subject in (intent["before"], data["after"]):
        if (
            type(subject) is not dict
            or set(subject) != {"observation", "candidate_sha256"}
            or candidate_sha256(_candidate(subject["observation"]))
            != subject["candidate_sha256"]
        ):
            raise ValueError("external subject mismatch")
    before = intent["before"]["observation"]
    after = data["after"]["observation"]
    if (
        before["repository_root"] != after["repository_root"]
        or before["repository_id"] != after["repository_id"]
    ):
        raise ValueError("external repository mismatch")
    cwd = Path(intent["working_directory"])
    root = Path(before["repository_root"])
    if (
        not cwd.is_absolute()
        or not cwd.is_relative_to(root)
        or ".." in cwd.parts
        or any(part.casefold() == ".git" for part in cwd.relative_to(root).parts)
    ):
        raise ValueError("invalid external working directory")
    for records in (intent["protected_before"], data["protected_after"]):
        if type(records) is not list or len(records) > 10000:
            raise ValueError("invalid external protected observations")
        paths = set()
        for record in records:
            if (
                type(record) is not dict
                or set(record) != {"path", "sha256"}
                or not Path(_text(record["path"])).is_absolute()
                or record["path"] in paths
            ):
                raise ValueError("invalid external protected identity")
            _digest(record["sha256"])
            paths.add(record["path"])
    if [row["path"] for row in intent["protected_before"]] != [
        row["path"] for row in data["protected_after"]
    ]:
        raise ValueError("external protected paths mismatch")
    subject_state = "unchanged" if intent["before"] == data["after"] else "changed"
    if data["subject_state"] != subject_state:
        raise ValueError("external subject state mismatch")
    if data["protected_state"] != (
        "unchanged"
        if intent["protected_before"] == data["protected_after"]
        else "changed"
    ):
        raise ValueError("external protected state mismatch")
    for key in ("created_at",):
        if datetime.fromisoformat(intent[key]).utcoffset() != UTC.utcoffset(None):
            raise ValueError("external timestamp requires UTC")
    start, end = (
        datetime.fromisoformat(data[key]) for key in ("started_at", "ended_at")
    )
    if (
        start.utcoffset() != UTC.utcoffset(None)
        or end.utcoffset() != UTC.utcoffset(None)
        or end < start
    ):
        raise ValueError("invalid external times")
    duration = data["duration_seconds"]
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise ValueError("invalid external duration")
    process = data["process"]
    if (
        type(process) is not dict
        or set(process) != {"state", "exit_code", "timed_out"}
        or type(process["timed_out"]) is not bool
        or (process["exit_code"] is not None and type(process["exit_code"]) is not int)
    ):
        raise ValueError("invalid external process")
    expected = (
        "TIMEOUT"
        if process["timed_out"]
        else "LAUNCH_ERROR"
        if process["exit_code"] is None
        else "EXITED"
    )
    if process["state"] != expected or (
        process["timed_out"] and process["exit_code"] is not None
    ):
        raise ValueError("external process mismatch")
    process_record = _json(_read(receipt_path.parent / "process.json"))
    if process_record != {
        key: data[key]
        for key in (
            "intent_sha256",
            "started_at",
            "ended_at",
            "duration_seconds",
            "process",
        )
    }:
        raise ValueError("external saved process mismatch")
    tests = _artifact(receipt_path.parent)
    if data["tests"] != tests:
        raise ValueError("external result changed or missing")
    outcome = (
        "TIMEOUT"
        if process["timed_out"]
        else "UNKNOWN"
        if process["exit_code"] is None
        else "FAIL"
        if process["exit_code"] != 0
        else tests["state"]
    )
    if not process["timed_out"] and (
        subject_state != "unchanged" or data["protected_state"] != "unchanged"
    ):
        outcome = "UNKNOWN"
    if data["outcome"] != outcome:
        raise ValueError("external outcome mismatch")
    return {
        **intent,
        **{
            key: data[key]
            for key in (
                "after",
                "started_at",
                "ended_at",
                "duration_seconds",
                "process",
                "tests",
                "subject_state",
                "protected_state",
                "outcome",
            )
        },
        "receipt": {"path": str(receipt_path), "sha256": _hash(raw)},
        "artifact_path": str(receipt_path.parent / "result.xml")
        if tests["artifact"] is not None
        else None,
        "limits": [
            "unauthenticated_metadata",
            "caller_display_not_exact_argv",
            "raw_xml_checker_owned_may_contain_private_data",
            "counts_not_semantic_proof",
            "point_in_time_not_os_sandbox",
            "not_native_execution",
        ],
    }

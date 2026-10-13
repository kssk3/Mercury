"""Pure failure assessment of H2 projections; never completion authority."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, cast


@dataclass(frozen=True)
class FailureFinding:
    kind: Literal[
        "unknown", "native", "observation", "scope", "protected_input", "verification"
    ]
    reason: str
    attribution: Literal["new", "unknown"] = "unknown"
    command_sha256: str | None = None


@dataclass(frozen=True)
class FailureAssessment:
    attempt_id: str | None
    revision: str | None
    findings: tuple[FailureFinding, ...]


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid_metadata")


def _mapping(value: object) -> dict[str, Any]:
    _require(type(value) is dict)
    return cast(dict[str, Any], value)


def _digest(value: object, lengths: tuple[int, ...] = (64,)) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and re.fullmatch(r"[0-9a-f]+", value) is not None
    )


def _boolean(value: object) -> bool:
    _require(type(value) is bool)
    return bool(value)


def _commands(value: object) -> list[dict[str, Any]]:
    _require(type(value) is list)
    result = []
    for item in cast(list[object], value):
        data = _mapping(item)
        _require(_digest(data["command_sha256"]))
        _require(data["exit_code"] is None or type(data["exit_code"]) is int)
        _boolean(data["timed_out"])
        result.append(data)
    return result


def _classify(attempt: object) -> FailureAssessment:
    data = _mapping(attempt)
    identity = data["attempt_id"]
    _require(
        isinstance(identity, str)
        and bool(identity)
        and identity == identity.strip()
        and identity.isprintable()
    )
    for key in ("repository_id", "contract_sha256", "context_sha256"):
        _require(_digest(data[key]))
    _require(isinstance(data["repository_root"], str) and bool(data["repository_root"]))
    for key in ("before_head", "after_head"):
        _require(data[key] is None or _digest(data[key], (40, 64)))
    findings: list[FailureFinding] = []

    def unknown(reason: str) -> None:
        findings.append(FailureFinding("unknown", reason))

    observation = data["observation"]
    stopping = False
    if observation is None:
        unknown("missing_observation")
    else:
        observed = _mapping(observation)
        _require(observed["status"] in ("observed", "failed"))
        if observed["status"] == "failed":
            _require(
                observed["classification"]
                in ("adapter_raised", "after_observation_failed")
            )
            findings.append(FailureFinding("observation", "observation_failed"))
            stopping = True
        process = observed["process"]
        if process is not None:
            process = _mapping(process)
            status, code = process["process_status"], process["exit_code"]
            _require(
                status in ("successful_exit", "nonzero_exit", "timeout", "launch_error")
            )
            _require(code is None or type(code) is int)
            _require(
                (status == "successful_exit" and code == 0)
                or (status == "nonzero_exit" and code is not None and code != 0)
                or status == "timeout"
                or (status == "launch_error" and code is None)
            )
            if status != "successful_exit":
                reason = {
                    "timeout": "native_timeout",
                    "nonzero_exit": "native_nonzero",
                    "launch_error": "native_launch_error",
                }[status]
                findings.append(FailureFinding("native", reason))
                stopping = True
            _require(
                process["final_output_status"]
                in ("available", "missing", "unsafe", "unreadable")
            )
            if process["final_output_status"] != "available" and not stopping:
                unknown("missing_output")
        elif not stopping:
            unknown("missing_process")
    scope = data["scope"]
    if scope is not None:
        scope = _mapping(scope)
        proceed = _boolean(scope["proceed"])
        unexplained = _boolean(scope["unexplained_status_change"])
        delta = _mapping(scope["delta"])
        head, index = _boolean(delta["head_changed"]), _boolean(delta["index_changed"])
        for key in ("out_of_scope_paths", "protected_paths"):
            _require(
                type(scope[key]) is list
                and all(isinstance(item, str) and item for item in scope[key])
            )
        violation = bool(
            scope["out_of_scope_paths"]
            or scope["protected_paths"]
            or unexplained
            or head
            or index
        )
        _require(proceed == (not violation))
        if violation:
            findings.append(FailureFinding("scope", "scope_violation"))
            stopping = True
    elif not stopping:
        unknown("missing_scope")
    if data["outputs"] is None:
        if not stopping:
            unknown("missing_output")
    else:
        outputs = _mapping(data["outputs"])
        _mapping(outputs["stdout"])
        _mapping(outputs["stderr"])
        final_message = outputs["final_message"]
        if final_message is not None:
            _mapping(final_message)
        if observation is not None:
            process_metadata = _mapping(observation)["process"]
            if process_metadata is not None:
                available = (
                    _mapping(process_metadata)["final_output_status"] == "available"
                )
                if available != (final_message is not None):
                    unknown("contradictory_output")
    report = data["verification"]
    if report is None:
        if not stopping:
            unknown("missing_verification")
    else:
        report = _mapping(report)
        for key in (
            "attempt_id",
            "repository_root",
            "repository_id",
            "contract_sha256",
            "context_sha256",
            "before_head",
        ):
            _require(report[key] == data[key])
        _require(
            data["after_head"] is not None
            and report["target_revision"] == data["after_head"]
        )
        _require(
            report["technical_kind"] in ("pass", "fail", "blocked")
            and report["overall_kind"] in ("pass", "fail", "blocked")
        )
        current, baseline = (
            _commands(report["commands"]),
            _commands(report["baseline_commands"]),
        )
        current_counts = Counter(item["command_sha256"] for item in current)
        baseline_counts = Counter(item["command_sha256"] for item in baseline)
        for command in current:
            digest = command["command_sha256"]
            if command["timed_out"] or command["exit_code"] not in (0, None):
                prior = next(
                    (item for item in baseline if item["command_sha256"] == digest),
                    None,
                )
                new = (
                    current_counts[digest] == baseline_counts[digest] == 1
                    and prior is not None
                    and prior["exit_code"] == 0
                    and not prior["timed_out"]
                )
                findings.append(
                    FailureFinding(
                        "verification",
                        "verification_timeout"
                        if command["timed_out"]
                        else "verification_nonzero",
                        "new" if new else "unknown",
                        digest,
                    )
                )
            elif command["exit_code"] is None:
                unknown("incomplete_verification")
        if not current:
            unknown("missing_verification_commands")
        _require(type(report["protected_inputs"]) is list)
        for item in report["protected_inputs"]:
            check = _mapping(item)
            _require(
                _digest(check["path_sha256"])
                and check["state"]
                in ("unchanged", "modified", "missing", "unverifiable")
            )
            if check["state"] == "unverifiable":
                unknown("protected_input_unverifiable")
            elif check["state"] != "unchanged":
                findings.append(
                    FailureFinding("protected_input", "protected_input_violation")
                )
        protected_failure = any(
            item["state"] != "unchanged" for item in report["protected_inputs"]
        )
        expected_overall = "fail" if protected_failure else report["technical_kind"]
        if report["overall_kind"] != expected_overall or (
            report["technical_kind"] != "pass"
            and all(
                item["exit_code"] == 0 and not item["timed_out"] for item in current
            )
        ):
            unknown("contradictory_verification")
    return FailureAssessment(identity, data["after_head"], tuple(findings))


def classify_attempt_failure(attempt: object) -> FailureAssessment:
    """Return fixed findings from ordinary JSON metadata without mutation or I/O."""
    try:
        return _classify(attempt)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return FailureAssessment(
            None, None, (FailureFinding("unknown", "invalid_metadata"),)
        )

"""Pure retry reservations over caller-supplied H3 evidence; no execution authority."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import cast

from agent_harness.failure import FailureAssessment, FailureFinding


def _integer(value: object, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _seconds(value: object, *, positive: bool) -> bool:
    try:
        if type(value) not in (int, float):
            return False
        number = cast(int | float, value)
        return math.isfinite(number) and (number > 0 if positive else number >= 0)
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass(frozen=True)
class RetryLimits:
    max_retries: int
    max_same_failure: int
    max_commands: int
    max_seconds: float

    def __post_init__(self) -> None:
        if not (
            _integer(self.max_retries, 0)
            and _integer(self.max_same_failure, 1)
            and _integer(self.max_commands, 1)
            and _seconds(self.max_seconds, positive=True)
        ):
            raise ValueError("invalid_input")


@dataclass(frozen=True)
class RetryUsage:
    commands: int
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if not (
            _integer(self.commands, 0)
            and _seconds(self.elapsed_seconds, positive=False)
        ):
            raise ValueError("invalid_input")


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    reason: str
    completed_retries: int
    repeated_failures: int


_REASONS = {
    "unknown": {
        "missing_observation",
        "missing_output",
        "missing_process",
        "missing_scope",
        "contradictory_output",
        "missing_verification",
        "incomplete_verification",
        "missing_verification_commands",
        "protected_input_unverifiable",
        "contradictory_verification",
        "invalid_metadata",
    },
    "native": {"native_timeout", "native_nonzero", "native_launch_error"},
    "observation": {"observation_failed"},
    "scope": {"scope_violation"},
    "protected_input": {"protected_input_violation"},
    "verification": {"verification_timeout", "verification_nonzero"},
}


def _digest(value: object, lengths: tuple[int, ...]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and re.fullmatch(r"[0-9a-f]+", value) is not None
    )


def _history(value: object) -> tuple[FailureAssessment, ...]:
    if type(value) not in (list, tuple) or not value:
        raise ValueError("invalid_history")
    assessments = cast(list[FailureAssessment] | tuple[FailureAssessment, ...], value)
    identities: set[str] = set()
    for assessment in assessments:
        if not isinstance(assessment, FailureAssessment):
            raise ValueError("invalid_history")
        identity = assessment.attempt_id
        if not (
            isinstance(identity, str)
            and identity
            and identity == identity.strip()
            and identity.isprintable()
            and identity not in identities
        ):
            raise ValueError("invalid_history")
        identities.add(identity)
        if assessment.revision is not None and not _digest(
            assessment.revision, (40, 64)
        ):
            raise ValueError("invalid_history")
        if type(assessment.findings) is not tuple:
            raise ValueError("invalid_history")
        for finding in assessment.findings:
            if (
                not isinstance(finding, FailureFinding)
                or finding.kind not in _REASONS
                or finding.reason not in _REASONS[finding.kind]
                or finding.attribution not in ("new", "unknown")
            ):
                raise ValueError("invalid_history")
            if finding.kind == "verification":
                if not _digest(finding.command_sha256, (64,)):
                    raise ValueError("invalid_history")
            elif finding.command_sha256 is not None or finding.attribution != "unknown":
                raise ValueError("invalid_history")
    return tuple(assessments)


def _signature(
    assessment: FailureAssessment,
) -> frozenset[tuple[str, str, str, str | None]]:
    return frozenset(
        (finding.kind, finding.reason, finding.attribution, finding.command_sha256)
        for finding in assessment.findings
    )


def evaluate_retry(
    history: object,
    *,
    limits: RetryLimits,
    usage: RetryUsage,
    next_commands: int,
    next_seconds: float,
) -> RetryDecision:
    """Reserve another attempt; caller owns complete same-task evidence and usage.

    Validation precedes all-history safety, no failure, retry/repetition caps,
    then command/time reservations. No history authentication or hard timeout.
    """
    try:
        if not isinstance(limits, RetryLimits) or not isinstance(usage, RetryUsage):
            raise ValueError("invalid_input")
        limits.__post_init__()
        usage.__post_init__()
        if not (_integer(next_commands, 1) and _seconds(next_seconds, positive=True)):
            raise ValueError("invalid_input")
    except (TypeError, ValueError, AttributeError, OverflowError):
        return RetryDecision(False, "invalid_input", 0, 0)
    try:
        assessments = _history(history)
        current = _signature(assessments[-1])
        repeated = sum(_signature(item) == current for item in assessments)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return RetryDecision(False, "invalid_history", 0, 0)
    completed = len(assessments) - 1

    def decision(reason: str) -> RetryDecision:
        return RetryDecision(reason == "retry_allowed", reason, completed, repeated)

    if any(
        finding.kind in ("unknown", "observation", "scope", "protected_input")
        or (finding.kind == "verification" and finding.attribution == "unknown")
        for item in assessments
        for finding in item.findings
    ):
        return decision("unsafe_failure")
    if not current:
        return decision("no_failure")
    if completed >= limits.max_retries:
        return decision("retry_budget")
    if repeated >= limits.max_same_failure:
        return decision("same_failure")
    if (
        usage.commands >= limits.max_commands
        or next_commands > limits.max_commands - usage.commands
    ):
        return decision("command_budget")
    if (
        usage.elapsed_seconds >= limits.max_seconds
        or next_seconds > limits.max_seconds - usage.elapsed_seconds
    ):
        return decision("time_budget")
    return decision("retry_allowed")

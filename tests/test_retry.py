from __future__ import annotations

import importlib
import importlib.util
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from agent_harness.failure import FailureAssessment, FailureFinding


def api() -> Any:
    assert importlib.util.find_spec("agent_harness.retry") is not None, (
        "H4 retry API is missing"
    )
    return importlib.import_module("agent_harness.retry")


def test_initial_native_failure_allows_retry_with_immutable_records() -> None:
    module = api()
    limits = module.RetryLimits(2, 3, 10, 20.0)
    usage = module.RetryUsage(1, 1.0)
    history = [
        FailureAssessment("one", None, (FailureFinding("native", "native_nonzero"),))
    ]
    decision = module.evaluate_retry(
        history, limits=limits, usage=usage, next_commands=1, next_seconds=1.0
    )
    assert (
        decision.retry,
        decision.reason,
        decision.completed_retries,
        decision.repeated_failures,
    ) == (True, "retry_allowed", 0, 1)
    for record, field in (
        (limits, "max_retries"),
        (usage, "commands"),
        (decision, "retry"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_retries", True),
        ("max_retries", -1),
        ("max_same_failure", 0),
        ("max_commands", 1.0),
        ("max_seconds", float("inf")),
        ("max_seconds", True),
        ("max_seconds", 0),
    ],
)
def test_invalid_limits_raise_fixed_error(field: str, value: object) -> None:
    module = api()
    values: dict[str, object] = dict(
        max_retries=2, max_same_failure=3, max_commands=10, max_seconds=20.0
    )
    values[field] = value
    with pytest.raises(ValueError, match="^invalid_input$"):
        module.RetryLimits(**values)


@pytest.mark.parametrize(
    "commands,seconds",
    [(True, 0), (-1, 0), (1.0, 0), (0, -1), (0, float("nan")), (0, True)],
)
def test_invalid_usage_raises_fixed_error(commands: object, seconds: object) -> None:
    module = api()
    with pytest.raises(ValueError, match="^invalid_input$"):
        module.RetryUsage(commands, seconds)


def assess(
    identity: str, findings: tuple[FailureFinding, ...] | None = None
) -> FailureAssessment:
    return FailureAssessment(
        identity,
        None,
        (FailureFinding("native", "native_nonzero"),) if findings is None else findings,
    )


def decide(history: object, **overrides: Any) -> Any:
    module = api()
    arguments = dict(
        limits=module.RetryLimits(5, 3, 10, 20.0),
        usage=module.RetryUsage(1, 1.0),
        next_commands=1,
        next_seconds=1.0,
    )
    arguments.update(overrides)
    return module.evaluate_retry(history, **arguments)


@pytest.mark.parametrize(
    "history",
    [
        None,
        [],
        {},
        [object()],
        [assess("one"), assess("one")],
        [assess("")],
        [assess(" hidden ")],
        [assess("bad\n")],
        [FailureAssessment("one", "invalid", ())],
        [FailureAssessment("one", None, cast(Any, []))],
        [assess("one", (FailureFinding("native", "invented"),))],
        [
            assess(
                "one",
                (FailureFinding("verification", "verification_nonzero", "new", "bad"),),
            )
        ],
    ],
)
def test_malformed_history_is_denied_without_echo(history: object) -> None:
    result = decide(history)
    assert (result.retry, result.reason) == (False, "invalid_history")


@pytest.mark.parametrize(
    "arguments",
    [
        dict(limits=None),
        dict(usage={}),
        dict(next_commands=True),
        dict(next_commands=0),
        dict(next_seconds=0),
        dict(next_seconds=float("nan")),
        dict(next_seconds=True),
    ],
)
def test_invalid_input_precedes_history(arguments: dict[str, Any]) -> None:
    result = decide([], **arguments)
    assert (result.retry, result.reason) == (False, "invalid_input")


@pytest.mark.parametrize(
    "finding",
    [
        FailureFinding("unknown", "missing_scope"),
        FailureFinding("observation", "observation_failed"),
        FailureFinding("scope", "scope_violation"),
        FailureFinding("protected_input", "protected_input_violation"),
        FailureFinding("verification", "verification_nonzero", "unknown", "a" * 64),
    ],
)
def test_earlier_stop_survives_later_success(finding: FailureFinding) -> None:
    result = decide([assess("old", (finding,)), assess("now", ())])
    assert (result.retry, result.reason) == (False, "unsafe_failure")


def test_no_failure_precedes_exhausted_retry_budget() -> None:
    module = api()
    assert (
        decide([assess("한글", ())], limits=module.RetryLimits(0, 1, 1, 1.0)).reason
        == "no_failure"
    )


def test_nonconsecutive_equivalence_ignores_order_duplicates_identity_and_revision() -> (
    None
):
    a = FailureFinding("native", "native_nonzero")
    b = FailureFinding("verification", "verification_nonzero", "new", "a" * 64)
    history = [
        assess("one", (a, b)),
        assess("two", (FailureFinding("native", "native_timeout"),)),
        FailureAssessment("three", "b" * 40, (b, a, a)),
        assess("four", (a, b)),
    ]
    result = decide(history)
    assert (
        result.retry,
        result.reason,
        result.completed_retries,
        result.repeated_failures,
    ) == (False, "same_failure", 3, 3)


def test_command_digest_difference_does_not_reset_retry_count_but_is_distinct() -> None:
    module = api()
    history = [
        assess(
            "one",
            (FailureFinding("verification", "verification_nonzero", "new", "a" * 64),),
        ),
        assess(
            "two",
            (FailureFinding("verification", "verification_nonzero", "new", "b" * 64),),
        ),
    ]
    result = decide(history, limits=module.RetryLimits(5, 2, 10, 20))
    assert (result.retry, result.completed_retries, result.repeated_failures) == (
        True,
        1,
        1,
    )


@pytest.mark.parametrize(
    "retries,same,commands,seconds,next_commands,next_seconds,reason",
    [
        (0, 1, 10, 20, 1, 1, "retry_budget"),
        (5, 1, 10, 20, 1, 1, "same_failure"),
        (5, 3, 10, 20, 10, 30, "command_budget"),
        (5, 3, 10, 20, 1, 20, "time_budget"),
        (5, 3, 10, 20, 9, 19, "retry_allowed"),
    ],
)
def test_budget_precedence_and_exact_remaining_reservations(
    retries: int,
    same: int,
    commands: int,
    seconds: float,
    next_commands: int,
    next_seconds: float,
    reason: str,
) -> None:
    module = api()
    result = decide(
        (assess("one"),),
        limits=module.RetryLimits(retries, same, commands, seconds),
        next_commands=next_commands,
        next_seconds=next_seconds,
    )
    assert result.reason == reason
    assert result.retry == (reason == "retry_allowed")


@pytest.mark.parametrize(
    "usage,reason", [((10, 1), "command_budget"), ((1, 20), "time_budget")]
)
def test_consumed_budget_denies_retry(usage: tuple[int, float], reason: str) -> None:
    module = api()
    assert decide([assess("one")], usage=module.RetryUsage(*usage)).reason == reason

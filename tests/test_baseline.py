from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.runner import CommandResult


def result(
    command: tuple[str, ...], *, exit_code: int | None, timed_out: bool = False
) -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        timed_out=timed_out,
    )


def test_baseline_retains_immutable_pre_mutation_results() -> None:
    captured = (result(("check",), exit_code=0),)

    baseline = VerificationBaseline.capture(captured)

    assert baseline.results == captured
    with pytest.raises(FrozenInstanceError):
        baseline.__setattr__("results", ())


def test_baseline_normalizes_directly_constructed_results_to_a_tuple() -> None:
    first = result(("first",), exit_code=0)
    caller_owned_results = [first]

    baseline = VerificationBaseline(
        cast(tuple[CommandResult, ...], caller_owned_results)
    )
    caller_owned_results.append(result(("later",), exit_code=0))

    assert baseline.results == (first,)


def test_baseline_classifies_each_later_result_once() -> None:
    new_failure = ("new-failure",)
    existing_failure = ("existing-failure",)
    recovered = ("recovered",)
    unchanged_success = ("unchanged-success",)
    baseline = VerificationBaseline.capture(
        (
            result(new_failure, exit_code=0),
            result(existing_failure, exit_code=2),
            result(recovered, exit_code=None, timed_out=True),
            result(unchanged_success, exit_code=0),
        )
    )
    current = (
        result(new_failure, exit_code=1),
        result(existing_failure, exit_code=2),
        result(recovered, exit_code=0),
        result(unchanged_success, exit_code=0),
    )

    delta = baseline.compare(current)

    assert delta.new_failures == (current[0],)
    assert delta.preexisting_failures == (current[1],)
    assert delta.recovered == (current[2],)
    assert delta.unchanged_successes == (current[3],)
    assert (
        len(delta.new_failures)
        + len(delta.preexisting_failures)
        + len(delta.recovered)
        + len(delta.unchanged_successes)
    ) == len(current)
    with pytest.raises(FrozenInstanceError):
        delta.__setattr__("new_failures", ())


def test_baseline_treats_timed_out_zero_exit_as_failure() -> None:
    command = ("timed-out-check",)
    baseline = VerificationBaseline.capture((result(command, exit_code=0),))

    delta = baseline.compare((result(command, exit_code=0, timed_out=True),))

    assert delta.new_failures[0].command == command


def test_baseline_treats_launch_failure_as_failure() -> None:
    command = ("launch-failure",)
    baseline = VerificationBaseline.capture((result(command, exit_code=0),))

    delta = baseline.compare((result(command, exit_code=None),))

    assert delta.new_failures[0].command == command


@pytest.mark.parametrize(
    ("baseline_commands", "current_commands"),
    (
        ((("first",),), (("different",),)),
        ((("first",), ("second",)), (("second",), ("first",))),
        ((("first",),), (("first",), ("second",))),
        ((("first",), ("second",)), (("first",),)),
    ),
    ids=("changed", "reordered", "added", "removed"),
)
def test_baseline_rejects_a_changed_command_sequence(
    baseline_commands: tuple[tuple[str, ...], ...],
    current_commands: tuple[tuple[str, ...], ...],
) -> None:
    baseline = VerificationBaseline.capture(
        tuple(result(command, exit_code=0) for command in baseline_commands)
    )

    with pytest.raises(ValueError, match="same command sequence"):
        baseline.compare(
            tuple(result(command, exit_code=0) for command in current_commands)
        )


def test_baseline_compares_duplicate_commands_by_position() -> None:
    command = ("duplicate",)
    baseline = VerificationBaseline.capture(
        (result(command, exit_code=0), result(command, exit_code=1))
    )
    current = (result(command, exit_code=1), result(command, exit_code=0))

    delta = baseline.compare(current)

    assert delta.new_failures == (current[0],)
    assert delta.recovered == (current[1],)

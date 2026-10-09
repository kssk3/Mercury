from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_harness.policy import VerificationPolicy


def test_policy_is_immutable_and_keeps_admitted_commands() -> None:
    policy = VerificationPolicy(
        commands=(("pytest", "-q"), ("ruff", "check", "src")),
        working_directory=".",
        timeout_seconds=60,
    )

    assert policy.commands[0] == ("pytest", "-q")
    with pytest.raises(FrozenInstanceError):
        policy.timeout_seconds = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("commands", "working_directory", "timeout_seconds", "message"),
    [
        ((), ".", 60, "command"),
        (((),), ".", 60, "command"),
        ((("pytest",),), "/tmp", 60, "relative"),
        ((("pytest",),), "../escape", 60, "parent"),
        ((("pytest",),), ".", 0, "positive"),
    ],
)
def test_policy_rejects_invalid_inputs(
    commands: tuple[tuple[str, ...], ...],
    working_directory: str,
    timeout_seconds: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        VerificationPolicy(commands, working_directory, timeout_seconds)


def test_policy_copies_caller_owned_nested_command_collections() -> None:
    command = ["pytest", "-q"]
    commands = [command]

    policy = VerificationPolicy(commands, ".", 60)  # type: ignore[arg-type]
    command.append("tests/special.py")
    commands.append(["ruff", "check"])

    assert policy.commands == (("pytest", "-q"),)


def test_policy_rejects_non_string_command_tokens() -> None:
    with pytest.raises(ValueError, match="command"):
        VerificationPolicy(((["pytest"],),), ".", 60)  # type: ignore[arg-type]


def test_policy_rejects_direct_string_command() -> None:
    with pytest.raises(ValueError, match="command"):
        VerificationPolicy(("pytest",), ".", 60)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "commands",
    [
        (("py\x00test",),),
        (("pytest", "tests\x00/test.py"),),
        (("pytest",), ("ruff", "check", "src\x00")),
    ],
    ids=("executable", "argument", "later-command"),
)
def test_policy_rejects_nul_command_tokens(
    commands: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="command"):
        VerificationPolicy(commands, ".", 60)


@pytest.mark.parametrize("working_directory", ["\x00", "tests/\x00working"])
def test_policy_rejects_nul_working_directory(working_directory: str) -> None:
    with pytest.raises(ValueError, match="working directory"):
        VerificationPolicy((("pytest",),), working_directory, 60)


@pytest.mark.parametrize(
    "timeout_seconds",
    [
        True,
        False,
        1.0,
        0.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "60",
        (),
        [],
        {},
        0,
        -1,
    ],
    ids=(
        "true",
        "false",
        "whole-float",
        "fractional-float",
        "nan",
        "infinity",
        "negative-infinity",
        "none",
        "string",
        "tuple",
        "list",
        "dict",
        "zero",
        "negative",
    ),
)
def test_policy_rejects_non_positive_integer_timeout(timeout_seconds: object) -> None:
    with pytest.raises(ValueError, match="positive"):
        VerificationPolicy((("pytest",),), ".", timeout_seconds)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout_seconds", [1, 60])
@pytest.mark.parametrize("working_directory", ["", " ", "src//quality", "src\\quality"])
def test_policy_preserves_valid_current_command_and_cwd_values(
    timeout_seconds: int, working_directory: str
) -> None:
    policy = VerificationPolicy((("pytest", " "),), working_directory, timeout_seconds)

    assert policy.commands == (("pytest", " "),)
    assert policy.working_directory == working_directory
    assert policy.timeout_seconds == timeout_seconds

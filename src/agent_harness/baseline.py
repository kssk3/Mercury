from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agent_harness.runner import CommandResult


@dataclass(frozen=True)
class VerificationDelta:
    new_failures: tuple[CommandResult, ...]
    preexisting_failures: tuple[CommandResult, ...]
    recovered: tuple[CommandResult, ...]
    unchanged_successes: tuple[CommandResult, ...]


@dataclass(frozen=True)
class VerificationBaseline:
    results: tuple[CommandResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))

    @classmethod
    def capture(cls, results: Iterable[CommandResult]) -> VerificationBaseline:
        return cls(tuple(results))

    def compare(self, current_results: Iterable[CommandResult]) -> VerificationDelta:
        current = tuple(current_results)
        if _commands(current) != _commands(self.results):
            raise ValueError("current results must use the same command sequence")

        new_failures: list[CommandResult] = []
        preexisting_failures: list[CommandResult] = []
        recovered: list[CommandResult] = []
        unchanged_successes: list[CommandResult] = []
        for previous, later in zip(self.results, current, strict=True):
            if _is_successful(previous):
                if _is_successful(later):
                    unchanged_successes.append(later)
                else:
                    new_failures.append(later)
            elif _is_successful(later):
                recovered.append(later)
            else:
                preexisting_failures.append(later)

        return VerificationDelta(
            new_failures=tuple(new_failures),
            preexisting_failures=tuple(preexisting_failures),
            recovered=tuple(recovered),
            unchanged_successes=tuple(unchanged_successes),
        )


def _commands(results: tuple[CommandResult, ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(result.command for result in results)


def _is_successful(result: CommandResult) -> bool:
    return (
        isinstance(result.exit_code, int)
        and not isinstance(result.exit_code, bool)
        and result.exit_code == 0
        and result.timed_out is False
    )

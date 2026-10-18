"""Minimal contract for trusted agent backends; not a hostile-code boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agent_harness.admission import AdmissionRecord
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract


@dataclass(frozen=True)
class TurnBudgets:
    timeout_seconds: float
    input_bytes: int
    stdout_bytes: int
    stderr_bytes: int
    final_bytes: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        for value in (
            self.input_bytes,
            self.stdout_bytes,
            self.stderr_bytes,
            self.final_bytes,
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("byte budgets must be positive integer bytes")


@dataclass(frozen=True)
class CapturedText:
    """UTF-8 with surrogateescape; re-encode with it to recover retained bytes.

    Invalid UTF-8 and partial final code points are preserved, not expanded into
    replacement characters. Text remains opaque, untrusted and unredacted.
    """

    text: str
    retained_bytes: int
    truncated: bool


@dataclass(frozen=True)
class AgentTurnResult:
    argv: tuple[str, ...]
    repository_root: str
    process_status: Literal[
        "launch_error", "timeout", "nonzero_exit", "successful_exit"
    ]
    exit_code: int | None
    duration_seconds: float
    stdout: CapturedText
    stderr: CapturedText
    final_message: CapturedText | None
    final_output_status: Literal["available", "missing", "unsafe", "unreadable"]


class AgentAdapter(Protocol):
    """Prepare before intent, then return bounded process facts from one run.

    Implementations are trusted code. This interface supplies no confinement or
    task-success authority; the harness still observes scope and verifies work.
    """

    def prepare(self, repository_root: Path) -> None: ...

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> AgentTurnResult: ...


def _validate_result(
    result: object, root: Path, budgets: TurnBudgets
) -> AgentTurnResult:
    """Reject unusable backend facts before metadata/storage or launch accounting."""
    if not isinstance(result, AgentTurnResult):
        raise ValueError("invalid_agent_result")
    if not (
        isinstance(result.argv, tuple)
        and all(isinstance(argument, str) for argument in result.argv)
        and result.repository_root == str(root)
        and result.process_status
        in ("launch_error", "timeout", "nonzero_exit", "successful_exit")
        and (result.exit_code is None or type(result.exit_code) is int)
        and type(result.duration_seconds) in (int, float)
        and math.isfinite(result.duration_seconds)
        and result.duration_seconds >= 0
        and result.final_output_status
        in ("available", "missing", "unsafe", "unreadable")
    ):
        raise ValueError("invalid_agent_result")
    if (
        (result.process_status == "successful_exit" and result.exit_code != 0)
        or (result.process_status == "nonzero_exit" and result.exit_code in (None, 0))
        or (result.process_status == "launch_error" and result.exit_code is not None)
        or (
            (result.final_output_status == "available")
            != (result.final_message is not None)
        )
    ):
        raise ValueError("invalid_agent_result")
    if not isinstance(result.stdout, CapturedText) or not isinstance(
        result.stderr, CapturedText
    ):
        raise ValueError("invalid_agent_result")
    for capture, limit in (
        (result.stdout, budgets.stdout_bytes),
        (result.stderr, budgets.stderr_bytes),
        (result.final_message, budgets.final_bytes),
    ):
        if capture is None:
            continue
        if not (
            isinstance(capture, CapturedText)
            and isinstance(capture.text, str)
            and type(capture.retained_bytes) is int
            and 0 <= capture.retained_bytes <= limit
            and type(capture.truncated) is bool
            and len(capture.text.encode("utf-8", errors="surrogateescape"))
            == capture.retained_bytes
        ):
            raise ValueError("invalid_agent_result")
    return result

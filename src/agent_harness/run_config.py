"""Explicit, hash-approved run configuration; no discovery or native execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agent_harness.agent import TurnBudgets
from agent_harness.contract import TaskContract
from agent_harness.loop import bind_completion_criteria
from agent_harness.policy import VerificationPolicy
from agent_harness.repository import (
    resolve_repository_root,
    resolve_repository_state_location,
    validate_runtime_state_directory,
)
from agent_harness.retry import RetryLimits

_LIMIT = 65536
_KEYS = {
    "schema_version",
    "repository",
    "expected_head",
    "state_root",
    "backend",
    "executable",
    "contract",
    "verification",
    "criterion_commands",
    "context_paths",
    "budgets",
    "total_timeout_seconds",
}


@dataclass(frozen=True)
class RunConfig:
    config_path: Path
    config_sha256: str
    approver: str
    repository: Path
    expected_head: str
    state_root: Path
    backend: str
    executable: Path
    contract: TaskContract
    policy: VerificationPolicy
    criterion_commands: object
    context_paths: tuple[str, ...]
    budgets: TurnBudgets
    limits: RetryLimits


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError
    return cast(dict[str, object], value)


def _strings(value: object, *, maximum: int = 128) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError
    if any(type(item) is not str or not item or "\x00" in item for item in value):
        raise ValueError
    return tuple(cast(list[str], value))


def _text(value: object) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError
    return value


def _number(value: object, maximum: float, *, integer: bool = False) -> float:
    if type(value) not in ((int,) if integer else (int, float)):
        raise ValueError
    number = cast(int | float, value)
    if not math.isfinite(number) or not 0 < number <= maximum:
        raise ValueError
    return number


def _absolute(value: object, *, canonical: bool = True) -> Path:
    text = _text(value)
    path = Path(text)
    if not path.is_absolute() or str(path) != text:
        raise ValueError
    if canonical:
        for ancestor in (*reversed(path.parents), path):
            if ancestor.is_symlink():
                raise ValueError
        if path.resolve() != path:
            raise ValueError
    return path


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError


def _read_config(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > _LIMIT
        ):
            raise ValueError
        raw = bytearray()
        while len(raw) <= _LIMIT:
            chunk = os.read(descriptor, min(8192, _LIMIT + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        if len(raw) > _LIMIT or after.st_nlink != 1 or after.st_size != len(raw):
            raise ValueError
        return bytes(raw)
    finally:
        os.close(descriptor)


def _covered(path: str, boundaries: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in boundaries)


def _program_matches(selected: Path, names: tuple[str, ...]) -> bool:
    """Recognize supported names and same-file aliases, not arbitrary binaries."""
    resolved = selected.resolve()
    if selected.name in names or resolved.name in names:
        return True
    for name in names:
        known = shutil.which(name, path=os.defpath)
        if known is not None and selected.exists() and selected.samefile(known):
            return True
    return False


def validate_run_verification(
    repository: Path, contract: TaskContract, policy: VerificationPolicy
) -> None:
    """Bind supported direct-script verifiers at both public entry points.

    Shell/env delegation and interpreter evaluation/module forms are unsupported.
    This is bounded argv admission, not classification of arbitrary executables.
    """
    directory = repository / policy.working_directory
    _absolute(str(directory))
    if not directory.is_dir() or not directory.resolve().is_relative_to(repository):
        raise ValueError("invalid verification directory")
    for command in policy.commands:
        selected = _absolute(command[0], canonical=False)
        resolved = selected.resolve()
        if resolved.is_relative_to(repository):
            raise ValueError("verification executable is inside repository")
        if _program_matches(
            selected, ("env", "sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish")
        ):
            raise ValueError("verifier delegation is unsupported")
        python = (
            re.fullmatch(r"python(?:[23](?:\.[0-9]+)?)?", resolved.name) is not None
            or re.fullmatch(r"python(?:[23](?:\.[0-9]+)?)?", selected.name) is not None
            or selected.samefile(sys.executable)
        )
        node = _program_matches(selected, ("node", "nodejs"))
        if python or node:
            flags = (
                {"-I", "-B", "-E", "-s", "-S", "-u", "-O", "-OO", "-q"}
                if python
                else {"--no-warnings", "--disable-proto=throw"}
            )
            operands = iter(command[1:])
            script = None
            for argument in operands:
                if argument == "--":
                    script = next(operands, None)
                    break
                if argument.startswith("-"):
                    if argument not in flags:
                        raise ValueError("unsupported interpreter verifier option")
                else:
                    script = argument
                    break
            if script is None or script == "-" or not (directory / script).is_file():
                raise ValueError("verification script must already exist")
        for argument in command[1:]:
            candidate = directory / argument
            if candidate.is_file() and candidate.resolve().is_relative_to(repository):
                relative = candidate.resolve().relative_to(repository).as_posix()
                if not _covered(relative, contract.protected_paths):
                    raise ValueError("verification input is not protected")


def load_run_config(
    path: Path | str, approval: str | None, approver: str | None
) -> RunConfig:
    """Validate without writes; errors never echo untrusted configuration values."""
    try:
        if (
            not isinstance(approval, str)
            or re.fullmatch(r"[0-9a-f]{64}", approval) is None
            or not isinstance(approver, str)
            or not approver.strip()
            or len(approver) > 128
            or not approver.isprintable()
        ):
            raise ValueError
        config_path = _absolute(os.fspath(path))
        raw = _read_config(config_path)
        if hashlib.sha256(raw).hexdigest() != approval:
            raise ValueError
        data = _object(
            json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            ),
            _KEYS,
        )
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError
        repository = _absolute(data["repository"])
        if resolve_repository_root(repository) != repository:
            raise ValueError
        head = _text(data["expected_head"])
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) is None:
            raise ValueError
        state_root = _absolute(data["state_root"])
        if repository.is_relative_to(state_root) or state_root.is_relative_to(
            repository
        ):
            raise ValueError
        if config_path.is_relative_to(repository) or config_path.is_relative_to(
            state_root
        ):
            raise ValueError
        resolve_repository_state_location(repository, state_root)
        validate_runtime_state_directory(config_path.parent, repository)
        backend = _text(data["backend"])
        if backend not in {"codex", "claude"}:
            raise ValueError
        executable = _absolute(data["executable"], canonical=False)
        if executable.is_relative_to(repository) or executable.resolve().is_relative_to(
            repository
        ):
            raise ValueError
        contract_data = _object(
            data["contract"],
            {"goal", "allowed_paths", "protected_paths", "completion_criteria"},
        )
        contract = TaskContract(
            _text(contract_data["goal"]),
            _strings(contract_data["allowed_paths"]),
            _strings(contract_data["protected_paths"]),
            _strings(contract_data["completion_criteria"]),
        )
        verification = _object(
            data["verification"], {"commands", "working_directory", "timeout_seconds"}
        )
        commands = verification["commands"]
        if not isinstance(commands, list) or not 1 <= len(commands) <= 8:
            raise ValueError
        policy = VerificationPolicy(
            tuple(_strings(command) for command in commands),
            _text(verification["working_directory"]),
            int(_number(verification["timeout_seconds"], 120, integer=True)),
        )
        for protected in contract.protected_paths:
            selected = repository / protected
            _absolute(str(selected))
            if not selected.exists():
                raise ValueError
        validate_run_verification(repository, contract, policy)
        bind_completion_criteria(contract, policy, data["criterion_commands"])
        context_paths = _strings(data["context_paths"])
        if len(context_paths) != len(set(context_paths)):
            raise ValueError
        for selected_context in context_paths:
            TaskContract._validate_paths((selected_context,))
            if not _covered(
                selected_context, contract.allowed_paths + contract.protected_paths
            ):
                raise ValueError
        budget_data = _object(
            data["budgets"],
            {
                "timeout_seconds",
                "input_bytes",
                "stdout_bytes",
                "stderr_bytes",
                "final_bytes",
            },
        )
        budgets = TurnBudgets(
            _number(budget_data["timeout_seconds"], 120),
            *(
                int(_number(budget_data[name], 1048576, integer=True))
                for name in (
                    "input_bytes",
                    "stdout_bytes",
                    "stderr_bytes",
                    "final_bytes",
                )
            ),
        )
        total = _number(data["total_timeout_seconds"], 240)
        # Match loop phase reservations and leave explicit preflight slack.
        required = (
            budgets.timeout_seconds
            + 2
            + 3 * len(policy.commands) * (policy.timeout_seconds + 2)
            + 5
        )
        if total < required:
            raise ValueError
        return RunConfig(
            config_path,
            approval,
            approver,
            repository,
            head,
            state_root,
            backend,
            executable,
            contract,
            policy,
            data["criterion_commands"],
            context_paths,
            budgets,
            RetryLimits(0, 1, 1 + 3 * len(policy.commands), total),
        )
    except (ValueError, OSError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("invalid_run_config") from None

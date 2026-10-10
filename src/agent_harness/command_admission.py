"""Caller-attributed commands with explicit, in-memory approval.

Source bytes establish attribution, not command safety or a parsed recommendation.
These checks are point-in-time checks, not a concurrent-filesystem sandbox.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from agent_harness.admission import AdmissionRecord
from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy
from agent_harness.repository import resolve_repository_root


def _relative_path(value: str, *, allow_dot: bool = False) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("path must be a nonempty NUL-free string")
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be relative without parent traversal")
    if str(path) != value or (value == "." and not allow_dot):
        raise ValueError("path must be canonical")


@dataclass(frozen=True)
class DiscoveredCommand:
    repository_root: str
    source_path: str
    source_sha256: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        root = self.repository_root
        if (
            not isinstance(root, str)
            or "\x00" in root
            or not Path(root).is_absolute()
            or str(Path(root)) != root
            or ".." in Path(root).parts
        ):
            raise ValueError("repository root must be canonical and absolute")
        _relative_path(self.source_path)
        _relative_path(self.working_directory, allow_dot=True)
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise ValueError("source hash must be a SHA-256 hex digest")
        if (
            not isinstance(self.argv, Sequence)
            or isinstance(self.argv, str)
            or not self.argv
            or any(
                not isinstance(arg, str) or not arg or "\x00" in arg
                for arg in self.argv
            )
        ):
            raise ValueError("argv must contain nonempty NUL-free string tokens")
        object.__setattr__(self, "argv", tuple(self.argv))
        if (
            not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive integer")


def _selected_path(root: Path, relative: str, *, directory: bool) -> Path:
    """Inspect every component without accepting any symlink in the selection."""
    current = root
    parts = Path(relative).parts
    try:
        for index, component in enumerate(parts):
            current /= component
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("selected source/cwd must not contain symlinks")
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise ValueError("selected source/cwd ancestor must be a directory")
        mode = current.lstat().st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise ValueError("selected source/cwd has the wrong file type")
    except OSError as error:
        raise ValueError("could not inspect selected source/cwd") from error
    return current


def _source_hash(root: Path, source_path: str) -> str:
    selected = _selected_path(root, source_path, directory=False)
    try:
        digest = hashlib.sha256()
        with selected.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        raise ValueError("could not read selected source") from error


def discover_command(
    repository_path: Path | str,
    *,
    source_path: str,
    argv: Sequence[str],
    working_directory: str = ".",
    timeout_seconds: int,
) -> DiscoveredCommand:
    """Hash one explicitly selected file; do not parse or execute its content."""
    root = resolve_repository_root(repository_path)
    # Validate all caller inputs before reading the selected source.
    candidate = DiscoveredCommand(
        str(root),
        source_path,
        "0" * 64,
        argv,  # type: ignore[arg-type]
        working_directory,
        timeout_seconds,
    )
    _selected_path(root, working_directory, directory=True)
    return DiscoveredCommand(
        str(root),
        source_path,
        _source_hash(root, source_path),
        candidate.argv,
        working_directory,
        timeout_seconds,
    )


@dataclass(frozen=True)
class CommandApproval:
    candidate: DiscoveredCommand
    contract_json: str
    approver: str
    approved_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, DiscoveredCommand):
            raise TypeError("candidate must be a DiscoveredCommand")
        AdmissionRecord(self.approver, self.contract_json, self.approved_at)


def record_command_approval(
    candidate: DiscoveredCommand,
    contract: TaskContract,
    *,
    approver: str,
    approved_at: str,
) -> CommandApproval:
    """Record trusted caller-supplied approval; this does not authenticate a human."""
    return CommandApproval(candidate, contract.to_json(), approver, approved_at)


def admit_command(
    repository_path: Path | str,
    candidate: DiscoveredCommand,
    contract: TaskContract,
    approval: CommandApproval | None,
) -> VerificationPolicy:
    """Revalidate exact approval and current source/cwd before policy conversion."""
    if not isinstance(approval, CommandApproval):
        raise TypeError("approval must be a CommandApproval")
    if approval.candidate != candidate:
        raise ValueError("approval does not match command")
    if approval.contract_json != contract.to_json():
        raise ValueError("approval does not match contract")
    root = resolve_repository_root(repository_path)
    if str(root) != candidate.repository_root:
        raise ValueError("repository does not match command")
    if _source_hash(root, candidate.source_path) != candidate.source_sha256:
        raise ValueError("source does not match approved bytes")
    _selected_path(root, candidate.working_directory, directory=True)
    return VerificationPolicy(
        (candidate.argv,), candidate.working_directory, candidate.timeout_seconds
    )

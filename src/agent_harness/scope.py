"""Immediate aggregate scope decisions, independent of process/task success."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
from agent_harness.admission import AdmissionRecord
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract, _path_key
from agent_harness.journal import EventJournal
from agent_harness.turn import (
    FileFingerprint,
    GitStatusEntry,
    ObservationDelta,
    ObservedTurnResult,
    RepositoryObservation,
    _append,
    compare_observations,
    run_observed_turn,
)


@dataclass(frozen=True)
class ScopeDecision:
    delta: ObservationDelta
    out_of_scope_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    unexplained_status_change: bool

    @property
    def proceed(self) -> bool:
        """Scope-clear only; never technical PASS or new action authority."""
        return not (
            self.out_of_scope_paths
            or self.protected_paths
            or self.delta.head_changed
            or self.delta.index_changed
            or self.unexplained_status_change
        )


def _matches(path: str, boundary: str) -> bool:
    path = _path_key(path, fold_case=False)
    boundary = _path_key(boundary, fold_case=False)
    return path == boundary or path.startswith(boundary + "/")


def evaluate_scope(
    contract: TaskContract,
    before: RepositoryObservation,
    after: RepositoryObservation,
) -> ScopeDecision:
    """Recompute net effects from snapshots; no Git or filesystem reads."""
    _validate_contract(contract)
    _validate_observation(before)
    _validate_observation(after)
    delta = compare_observations(before, after)
    paths = sorted(set(delta.added + delta.modified + delta.deleted), key=os.fsencode)
    protected = tuple(
        path
        for path in paths
        if any(
            _matches(_path_key(path), _path_key(item))
            for item in contract.protected_paths
        )
    )
    changed_symlinks = {
        entry.path
        for entry in after.files
        if entry.kind == "symlink" and entry.path in delta.added + delta.modified
    }
    outside = tuple(
        path
        for path in paths
        if path not in protected
        and (
            path in changed_symlinks
            or not any(_matches(path, item) for item in contract.allowed_paths)
        )
    )
    return ScopeDecision(
        delta,
        outside,
        protected,
        delta.status_changed
        and not (paths or delta.head_changed or delta.index_changed),
    )


def _valid_path(path: object) -> bool:
    if not isinstance(path, str) or not path or "\0" in path:
        return False
    parsed = PurePosixPath(path)
    return (
        bool(parsed.parts)
        and not parsed.is_absolute()
        and str(parsed) == path
        and all(
            part not in {".", ".."} and part.casefold() != ".git"
            for part in parsed.parts
        )
    )


def _digest(value: object, lengths: tuple[int, ...] = (64,)) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and re.fullmatch(r"[0-9a-f]+", value) is not None
    )


def _validate_contract(contract: TaskContract) -> None:
    if not isinstance(contract, TaskContract):
        raise TypeError("invalid scope contract")
    if (
        not isinstance(contract.goal, str)
        or not isinstance(contract.completion_criteria, tuple)
        or not all(isinstance(item, str) for item in contract.completion_criteria)
    ):
        raise ValueError("invalid scope contract")
    for paths in (contract.allowed_paths, contract.protected_paths):
        if not isinstance(paths, tuple) or not all(_valid_path(path) for path in paths):
            raise ValueError("invalid scope contract")
        if len(paths) != len({_path_key(path) for path in paths}):
            raise ValueError("invalid scope contract")
    try:
        contract._validate()
    except (TypeError, ValueError):
        raise ValueError("invalid scope contract") from None


def _valid_head_ref(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("refs/"):
        return False
    return (
        not any(ord(character) < 33 or ord(character) == 127 for character in value)
        and not any(character in value for character in "~^:?*[\\")
        and ".." not in value
        and "@{" not in value
        and not value.endswith(".")
        and all(
            part and not part.startswith(".") and not part.endswith(".lock")
            for part in value.split("/")
        )
    )


def _validate_observation(snapshot: RepositoryObservation) -> None:
    if not isinstance(snapshot, RepositoryObservation):
        raise TypeError("invalid scope observation")
    root = snapshot.repository_root
    if (
        not isinstance(root, str)
        or "\0" in root
        or not root.startswith("/")
        or root.startswith("//")
        or str(PurePosixPath(root)) != root
        or ".." in PurePosixPath(root).parts
        or snapshot.repository_id != hashlib.sha256(os.fsencode(root)).hexdigest()
        or not _digest(snapshot.index_sha256)
        or (snapshot.head is not None and not _digest(snapshot.head, (40, 64)))
        or (snapshot.head_ref is not None and not _valid_head_ref(snapshot.head_ref))
        or not isinstance(snapshot.files, tuple)
        or not isinstance(snapshot.status, tuple)
    ):
        raise ValueError("invalid scope observation")
    names: set[str] = set()
    identities: dict[str, str] = {}
    for entry in snapshot.files:
        if (
            not isinstance(entry, FileFingerprint)
            or not _valid_path(entry.path)
            or _path_key(entry.path, fold_case=False) in identities
        ):
            raise ValueError("invalid scope observation")
        names.add(entry.path)
        identities[_path_key(entry.path, fold_case=False)] = entry.path
        if entry.kind == "missing":
            valid = (
                entry.sha256 is None
                and entry.size_bytes is None
                and entry.executable is None
            )
        else:
            valid = (
                entry.kind in {"regular", "symlink"}
                and _digest(entry.sha256)
                and type(entry.size_bytes) is int
                and entry.size_bytes >= 0
                and (
                    type(entry.executable) is bool
                    if entry.kind == "regular"
                    else entry.executable is None
                )
            )
        if entry.mode is not None:
            valid = (
                valid
                and entry.kind == "regular"
                and type(entry.mode) is int
                and 0 <= entry.mode <= 0o7777
                and entry.executable is bool(entry.mode & 0o111)
            )
        if not valid:
            raise ValueError("invalid scope observation")
    statuses: set[GitStatusEntry] = set()
    codes_by_path: dict[str, set[str]] = {}
    for status_entry in snapshot.status:
        if (
            not isinstance(status_entry, GitStatusEntry)
            or not _valid_path(status_entry.path)
            or (status_entry.path not in names and status_entry.status != "D ")
            or status_entry in statuses
            or not isinstance(status_entry.status, str)
            or len(status_entry.status) != 2
            or not (
                status_entry.status == "??"
                or (
                    status_entry.status != "  "
                    and all(character in " MADUT" for character in status_entry.status)
                )
            )
        ):
            raise ValueError("invalid scope observation")
        identity = _path_key(status_entry.path, fold_case=False)
        if identity in identities and identities[identity] != status_entry.path:
            raise ValueError("invalid scope observation")
        identities[identity] = status_entry.path
        statuses.add(status_entry)
        codes_by_path.setdefault(status_entry.path, set()).add(status_entry.status)
    for codes in codes_by_path.values():
        if len(codes) > 1 and codes != {"D ", "??"}:
            raise ValueError("invalid scope observation")


@dataclass(frozen=True)
class ScopedTurnResult:
    observed_turn: ObservedTurnResult
    decision: ScopeDecision


class ScopeTurnError(RuntimeError):
    """A complete F2 result remains in memory, but scope clearance failed."""

    def __init__(
        self,
        code: Literal["evaluation_failed", "decision_append_failed"],
        observed_turn: ObservedTurnResult,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.observed_turn = observed_turn
        self.proceed = False


def run_scoped_turn(
    adapter: CodexCLIAdapter,
    contract: TaskContract,
    admission: AdmissionRecord,
    context: ContextPacket,
    *,
    journal: EventJournal,
    budgets: TurnBudgets,
    sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
) -> ScopedTurnResult:
    """Observe once, evaluate, then close one metadata-only scope decision.

    F2 validation/observation errors propagate. Caller owns serialization/B4
    lock; there is no retry, rollback, next turn or task-success inference.
    """
    _validate_contract(contract)
    observed = run_observed_turn(
        adapter,
        contract,
        admission,
        context,
        journal=journal,
        budgets=budgets,
        sandbox=sandbox,
    )
    try:
        decision = evaluate_scope(contract, observed.before, observed.after)
    except Exception:
        raise ScopeTurnError("evaluation_failed", observed) from None
    try:
        _append(
            journal,
            Path(observed.after.repository_root),
            {
                "event": "scope_decision",
                "attempt_id": observed.attempt_id,
                "repository_id": observed.after.repository_id,
                **asdict(decision),
                "proceed": decision.proceed,
            },
        )
    except Exception:
        raise ScopeTurnError("decision_append_failed", observed) from None
    return ScopedTurnResult(observed, decision)

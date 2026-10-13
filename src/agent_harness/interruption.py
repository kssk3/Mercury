"""Read-only native interruption evidence and caller-attested human requests.

Caller owns B4 serialization. Reports are point-in-time observations, not atomic
snapshots, authentication, technical PASS or native session/resume identity.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from agent_harness.attempt import AttemptJournal
from agent_harness.journal import EventJournal
from agent_harness.scope import _digest
from agent_harness.startup import (
    StartupDecision,
    _bytes,
    _safe_storage,
    reconcile_startup,
)
from agent_harness.state import CurrentStateStore
from agent_harness.turn import RepositoryObservation


@dataclass(frozen=True)
class InterruptionReport:
    repository_id: str | None
    expected_contract_sha256: str
    evidence_sha256: str | None
    request_context_sha256: str
    evidence: StartupDecision
    report_sha256: str


@dataclass(frozen=True)
class HumanGateDecision:
    status: str
    action: str | None = None
    diagnostics: tuple[str, ...] = ()
    execution_authorized: bool = False


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _identity(report: InterruptionReport) -> str:
    return _hash(
        {
            "repository_id": report.repository_id,
            "expected_contract_sha256": report.expected_contract_sha256,
            "evidence_sha256": report.evidence_sha256,
            "request_context_sha256": report.request_context_sha256,
            "evidence": asdict(report.evidence),
        }
    )


def _boundary(attempts: AttemptJournal) -> tuple[str, str]:
    _safe_storage(attempts)
    observation = RepositoryObservation.capture(attempts.root)
    storage = []
    for path in (attempts.journal.path, attempts.state_store.path):
        raw = _bytes(path)
        storage.append(
            (str(path), None if raw is None else hashlib.sha256(raw).hexdigest())
        )
    return observation.repository_id, _hash(
        {"storage": storage, "workspace": asdict(observation)}
    )


def report_interruption(
    repository: Path | str,
    *,
    journal: EventJournal,
    state_store: CurrentStateStore,
    expected_contract_sha256: str,
) -> InterruptionReport:
    """Reuse R1 validation; hashes bind safe evidence without exposing contents.

    No agent, verification, recovery, native resume or write is performed. A
    caller must obtain a new report after any input change before human_gate.
    """
    # Input selectors bind requests even when their evidence cannot be read.
    # This opaque context hash does not assert storage safety or observation.
    context_sha256 = _hash(
        {
            "repository": str(repository),
            "journal": str(journal.path),
            "state": str(state_store.path),
        }
    )
    repository_id: str | None = None
    boundary: tuple[str, str] | None = None
    attempts: AttemptJournal | None = None
    try:
        attempts = AttemptJournal(repository, journal=journal, state_store=state_store)
        repository_id = hashlib.sha256(os.fsencode(attempts.root)).hexdigest()
        boundary = _boundary(attempts)
    except Exception:
        pass
    evidence = reconcile_startup(
        repository,
        journal=journal,
        state_store=state_store,
        expected_contract_sha256=expected_contract_sha256,
    )
    try:
        if attempts is None or boundary is None or boundary != _boundary(attempts):
            raise ValueError("unstable_boundary")
    except Exception:
        # Retain the R1 validated prefix; uncertain accounting cannot authorize work.
        evidence = replace(
            evidence,
            status="unknown",
            diagnostics=("evidence_unavailable_or_changed",),
            usage=None,
            accounting_complete=False,
        )
        boundary = None
    report = InterruptionReport(
        repository_id,
        expected_contract_sha256,
        boundary[1] if boundary else None,
        context_sha256,
        evidence,
        "",
    )
    return replace(report, report_sha256=_identity(report))


def human_gate(
    current_report: InterruptionReport, response: object
) -> HumanGateDecision:
    """Interpret caller-attested intent only; caller supplies the CURRENT report.

    Stop/inspection are distinct from requests requiring normal new admission or
    explicit R2 preflight/invocation. No request grants execution authority.
    """
    try:
        if (
            type(current_report) is not InterruptionReport
            or type(current_report.evidence) is not StartupDecision
            or not _digest(current_report.expected_contract_sha256)
            or not _digest(current_report.request_context_sha256)
            or not _digest(current_report.report_sha256)
            or current_report.evidence.status
            not in {"fresh", "completed", "interrupted", "unknown"}
            or current_report.report_sha256 != _identity(current_report)
        ):
            return HumanGateDecision("blocked", diagnostics=("invalid_report",))
        if type(response) is not dict or set(response) != {"report_sha256", "action"}:
            return HumanGateDecision("blocked", diagnostics=("malformed_response",))
        if response["report_sha256"] != current_report.report_sha256:
            return HumanGateDecision("blocked", diagnostics=("stale_or_wrong_report",))
        action = response["action"]
        if type(action) is not str or action not in {
            "stop",
            "inspect",
            "new_attempt",
            "recover_patch",
        }:
            return HumanGateDecision("blocked", diagnostics=("unsupported_action",))
        if action in {"stop", "inspect"}:
            return HumanGateDecision(action, action)
        if (
            current_report.evidence.status == "unknown"
            or not _digest(current_report.repository_id)
            or not _digest(current_report.evidence_sha256)
        ):
            return HumanGateDecision("blocked", action, ("unknown_evidence",))
        requirement = (
            "normal_admission_required"
            if action == "new_attempt"
            else "explicit_r2_validation_and_invocation_required"
        )
        return HumanGateDecision("requested", action, (requirement,))
    except Exception:
        return HumanGateDecision("blocked", diagnostics=("invalid_report_or_response",))

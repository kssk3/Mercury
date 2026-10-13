from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_harness.admission import admit, record_admission
from agent_harness.contract import TaskContract


def test_admission_accepts_a_record_for_the_exact_contract() -> None:
    contract = TaskContract("Task", (), (), ("tests",))
    record = record_admission(
        contract,
        approver="synthetic-approver",
        approved_at="2026-09-18T09:30:00+09:00",
    )

    assert admit(contract, record) is contract


def test_admission_serializes_the_contract_bound_to_the_record() -> None:
    contract = TaskContract("Task", ("src/a.py",), (), ("tests",))
    record = record_admission(
        contract,
        approver="synthetic-approver",
        approved_at="2026-09-18T09:30:00+09:00",
    )

    assert json.loads(admit(contract, record).to_json()) == {
        "allowed_paths": ["src/a.py"],
        "completion_criteria": ["tests"],
        "goal": "Task",
        "protected_paths": [],
    }


def test_admission_rejects_a_record_for_a_different_contract() -> None:
    approved_contract = TaskContract("Task", (), (), ("tests",))
    amended_contract = TaskContract("Task", ("src/a.py",), (), ("tests",))
    record = record_admission(
        approved_contract,
        approver="synthetic-approver",
        approved_at="2026-09-18T09:30:00+09:00",
    )

    with pytest.raises(ValueError, match="does not match"):
        admit(amended_contract, record)


@pytest.mark.parametrize(
    ("approver", "approved_at"),
    [
        ("", "2026-09-18T09:30:00+09:00"),
        ("  ", "2026-09-18T09:30:00+09:00"),
        ("synthetic-approver", "not-a-time"),
        ("synthetic-approver", "2026-09-18T09:30:00"),
    ],
)
def test_admission_record_rejects_invalid_provenance(
    approver: str, approved_at: str
) -> None:
    contract = TaskContract("Task", (), (), ("tests",))

    with pytest.raises(ValueError):
        record_admission(contract, approver=approver, approved_at=approved_at)


def test_admission_rejects_a_lookalike_record() -> None:
    contract = TaskContract("Task", (), (), ("tests",))
    lookalike = SimpleNamespace(contract_json=contract.to_json())

    with pytest.raises(TypeError, match="AdmissionRecord"):
        admit(contract, lookalike)  # type: ignore[arg-type]


def test_admission_record_is_immutable() -> None:
    contract = TaskContract("Task", (), (), ("tests",))
    record = record_admission(
        contract,
        approver="synthetic-approver",
        approved_at="2026-09-18T09:30:00+09:00",
    )

    with pytest.raises(AttributeError):
        record.approver = "other"  # type: ignore[misc]

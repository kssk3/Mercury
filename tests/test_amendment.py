from __future__ import annotations

import pytest

from agent_harness.amendment import AmendmentMateriality, classify_amendment
from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy


@pytest.mark.parametrize(
    "amended",
    [
        TaskContract("Changed task", (), (), ("tests",)),
        TaskContract("Task", ("src/a.py",), (), ("tests",)),
        TaskContract("Task", (), ("src/a.py",), ("tests",)),
        TaskContract("Task", (), (), ("tests", "lint")),
    ],
)
def test_contract_changes_require_readmission(amended: TaskContract) -> None:
    original = TaskContract("Task", (), (), ("tests",))
    policy = VerificationPolicy((("pytest", "-q"),), ".", 60)

    assert (
        classify_amendment(original, amended, policy, policy)
        is AmendmentMateriality.REQUIRES_READMISSION
    )


def test_identical_contract_requires_no_readmission() -> None:
    contract = TaskContract("Task", (), (), ("tests",))
    policy = VerificationPolicy((("pytest", "-q"),), ".", 60)

    assert (
        classify_amendment(contract, contract, policy, policy)
        is AmendmentMateriality.NO_CHANGE
    )


def test_verification_policy_change_requires_readmission() -> None:
    contract = TaskContract("Task", (), (), ("tests",))
    original_policy = VerificationPolicy((("pytest", "-q"),), ".", 60)
    amended_policy = VerificationPolicy((("pytest", "-q"),), ".", 30)

    assert (
        classify_amendment(contract, contract, original_policy, amended_policy)
        is AmendmentMateriality.REQUIRES_READMISSION
    )


@pytest.mark.parametrize("invalid_policy", [None, "not a policy"])
def test_invalid_policy_snapshots_cannot_be_classified(invalid_policy: object) -> None:
    contract = TaskContract("Task", (), (), ("tests",))

    with pytest.raises(TypeError, match="policy"):
        classify_amendment(
            contract,
            contract,
            invalid_policy,  # type: ignore[arg-type]
            invalid_policy,  # type: ignore[arg-type]
        )

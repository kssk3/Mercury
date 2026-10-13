from __future__ import annotations

from enum import StrEnum

from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy


class AmendmentMateriality(StrEnum):
    NO_CHANGE = "no_change"
    REQUIRES_READMISSION = "requires_readmission"


def classify_amendment(
    original: TaskContract,
    amended: TaskContract,
    original_policy: VerificationPolicy,
    amended_policy: VerificationPolicy,
) -> AmendmentMateriality:
    if not isinstance(original_policy, VerificationPolicy) or not isinstance(
        amended_policy, VerificationPolicy
    ):
        raise TypeError("both verification policy snapshots are required")
    if original == amended and original_policy == amended_policy:
        return AmendmentMateriality.NO_CHANGE
    return AmendmentMateriality.REQUIRES_READMISSION

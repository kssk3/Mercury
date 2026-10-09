from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from agent_harness.contract import TaskContract


@dataclass(frozen=True)
class AdmissionRecord:
    approver: str
    contract_json: str
    approved_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.approver, str) or not self.approver.strip():
            raise ValueError("Admission approver must be non-empty.")
        if not isinstance(self.contract_json, str) or not self.contract_json:
            raise ValueError("Admission record must contain contract JSON.")

        try:
            contract_data = json.loads(self.contract_json)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Admission record must contain valid contract JSON."
            ) from error
        if not isinstance(contract_data, dict):
            raise ValueError("Admission record contract JSON must be an object.")

        if not isinstance(self.approved_at, str):
            raise ValueError("Admission time must be an ISO-8601 string.")
        try:
            approved_time = datetime.fromisoformat(self.approved_at)
        except ValueError as error:
            raise ValueError(
                "Admission time must be a valid ISO-8601 timestamp."
            ) from error
        if approved_time.tzinfo is None or approved_time.utcoffset() is None:
            raise ValueError("Admission time must include a timezone.")


def record_admission(
    contract: TaskContract, *, approver: str, approved_at: str
) -> AdmissionRecord:
    return AdmissionRecord(
        approver=approver,
        contract_json=contract.to_json(),
        approved_at=approved_at,
    )


def admit(contract: TaskContract, record: AdmissionRecord) -> TaskContract:
    if not isinstance(record, AdmissionRecord):
        raise TypeError("Admission record must be an AdmissionRecord.")
    if record.contract_json != contract.to_json():
        raise ValueError("Admission record does not match the task contract.")
    return contract

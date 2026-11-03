"""Versioned recorded identities; no authentication or live workspace access."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Literal, cast

from agent_harness.review import ReviewBinding, _require_hex
from agent_harness.scope import _validate_observation
from agent_harness.turn import FileFingerprint, GitStatusEntry, RepositoryObservation

_FIELDS = (
    "event",
    "schema_version",
    "attempt_id",
    "repository_root",
    "repository_id",
    "contract_sha256",
    "context_sha256",
    "before_head",
    "target_revision",
    "technical_kind",
    "overall_kind",
    "commands",
    "baseline_commands",
    "reason_command_sha256",
    "protected_inputs",
    "output_identifiers",
)


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("invalid_review_evidence")
    return cast(dict[str, object], value)


def _candidate(value: object) -> RepositoryObservation:
    data = _mapping(value)
    expected = {
        "repository_root",
        "repository_id",
        "files",
        "head",
        "index_sha256",
        "status",
        "head_ref",
    }
    if set(data) != expected or not isinstance(data["files"], (tuple, list)):
        raise ValueError("invalid_review_candidate")
    files = []
    for value in cast(list[object], data["files"]):
        item = _mapping(value)
        if set(item) != {
            "path",
            "kind",
            "sha256",
            "size_bytes",
            "executable",
            "mode",
        }:
            raise ValueError("invalid_review_candidate")
        files.append(
            FileFingerprint(
                cast(str, item["path"]),
                cast(Literal["regular", "symlink", "missing"], item["kind"]),
                cast(str | None, item["sha256"]),
                cast(int | None, item["size_bytes"]),
                cast(bool | None, item["executable"]),
                cast(int | None, item["mode"]),
            )
        )
    if not isinstance(data["status"], (tuple, list)):
        raise ValueError("invalid_review_candidate")
    statuses = []
    for value in cast(list[object], data["status"]):
        item = _mapping(value)
        if set(item) != {"path", "status"}:
            raise ValueError("invalid_review_candidate")
        statuses.append(
            GitStatusEntry(cast(str, item["path"]), cast(str, item["status"]))
        )
    observation = RepositoryObservation(
        cast(str, data["repository_root"]),
        cast(str, data["repository_id"]),
        tuple(files),
        cast(str | None, data["head"]),
        cast(str, data["index_sha256"]),
        tuple(statuses),
        cast(str | None, data["head_ref"]),
    )
    _validate_observation(observation)
    return observation


def candidate_sha256(observation: RepositoryObservation) -> str:
    """Identify observed content without recapturing a mutable workspace."""
    _validate_observation(observation)
    candidate = asdict(observation)
    candidate["files"] = sorted(candidate["files"], key=lambda item: item["path"])
    candidate["status"] = sorted(candidate["status"], key=lambda item: item["path"])
    return _digest({"version": 1, "observation": candidate})


def _binding(
    verification: dict[str, object],
    observation: RepositoryObservation,
    verification_sha256: str,
) -> ReviewBinding:
    _require_hex(verification_sha256, (64,))
    if (
        type(verification["schema_version"]) is not int
        or verification["schema_version"] != 2
        or verification["event"] != "attempt_verification"
        or observation.repository_root != verification["repository_root"]
        or observation.repository_id != verification["repository_id"]
        or observation.head != verification["target_revision"]
    ):
        raise ValueError("review_candidate_identity_mismatch")
    candidate = candidate_sha256(observation)
    evidence = _digest(
        {
            "version": 1,
            "candidate_sha256": candidate,
            "verification_sha256": verification_sha256,
            "verification": {key: verification[key] for key in _FIELDS},
        }
    )
    return ReviewBinding(
        verification["repository_root"],
        cast(str, verification["target_revision"]),
        cast(str, verification["attempt_id"]),
        cast(str, verification["contract_sha256"]),
        candidate,
        evidence,
    )


def build_review_evidence(
    verification: dict[str, object],
    observation: RepositoryObservation,
    verification_sha256: str,
) -> dict[str, object]:
    """Caller supplies the actual unchanged final-verification observation."""
    binding = _binding(verification, observation, verification_sha256)
    return {
        "schema_version": 1,
        "candidate": asdict(observation),
        "verification_sha256": verification_sha256,
        "binding": asdict(binding),
    }


def read_review_binding(
    verification: dict[str, object], value: object
) -> ReviewBinding:
    """Validate saved identities; never open or execute opaque references."""
    data = _mapping(value)
    if (
        set(data) != {"schema_version", "candidate", "verification_sha256", "binding"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("invalid_review_evidence")
    binding = _binding(
        verification,
        _candidate(data["candidate"]),
        cast(str, data["verification_sha256"]),
    )
    if _mapping(data["binding"]) != asdict(binding):
        raise ValueError("review_binding_mismatch")
    return binding

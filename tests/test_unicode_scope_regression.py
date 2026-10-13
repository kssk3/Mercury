from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agent_harness.contract import TaskContract
from agent_harness.recovery import capture_patch
from agent_harness.scope import evaluate_scope
from agent_harness.turn import (
    FileFingerprint,
    RepositoryObservation,
    _reject_allowed_symlinks,
)


@pytest.mark.parametrize(
    "field", ["allowed_paths", "protected_paths", "completion_criteria"]
)
@pytest.mark.parametrize("scalar", ["src", b"src"])
def test_contract_rejects_scalar_collection(field: str, scalar: str | bytes) -> None:
    values: dict[str, object] = {
        "goal": "Task",
        "allowed_paths": (),
        "protected_paths": (),
        "completion_criteria": ("verified",),
    }
    values[field] = scalar
    with pytest.raises(ValueError, match="collection"):
        TaskContract(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["allowed_paths", "protected_paths"])
def test_contract_rejects_unicode_duplicate_boundaries(field: str) -> None:
    values: dict[str, object] = {
        "goal": "Task",
        "allowed_paths": (),
        "protected_paths": (),
        "completion_criteria": ("verified",),
    }
    values[field] = ("Caf\u00e9", "CAFE\u0301")
    with pytest.raises(ValueError, match="duplicate"):
        TaskContract(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "allowed,protected",
    [("Caf\u00e9", "CAFE\u0301/private"), ("Cafe\u0301/private", "CAF\u00c9")],
)
def test_contract_rejects_unicode_overlapping_boundaries(
    allowed: str, protected: str
) -> None:
    with pytest.raises(ValueError, match="both"):
        TaskContract("Task", (allowed,), (protected,), ("verified",))


def observation(root: Path) -> RepositoryObservation:
    name = str(root.resolve())
    return RepositoryObservation(
        name, hashlib.sha256(os.fsencode(name)).hexdigest(), (), None, "0" * 64, ()
    )


@pytest.mark.parametrize("protected", [False, True])
def test_scope_matches_unicode_equivalence_and_preserves_path(
    tmp_path: Path, protected: bool
) -> None:
    before = observation(tmp_path)
    path = "Cafe\u0301/file"
    after = replace(
        before, files=(FileFingerprint(path, "regular", "1" * 64, 1, False),)
    )
    contract = TaskContract(
        "Task",
        () if protected else ("Caf\u00e9",),
        ("CAF\u00c9",) if protected else (),
        ("verified",),
    )
    result = evaluate_scope(contract, before, after)
    assert result.proceed is not protected
    assert result.protected_paths == ((path,) if protected else ())
    assert result.out_of_scope_paths == ()
    assert result.delta.added == (path,)


@pytest.mark.parametrize("path", ["Cafe\u0301", "Cafe\u0301/link"])
def test_pre_native_rejects_unicode_equivalent_observed_symlink(
    tmp_path: Path, path: str
) -> None:
    snapshot = replace(
        observation(tmp_path),
        files=(FileFingerprint(path, "symlink", "1" * 64, 1, None),),
    )
    allowed = "Caf\u00e9/file" if path == "Cafe\u0301" else "Caf\u00e9"
    contract = TaskContract("Task", (allowed,), (), ("verified",))
    with pytest.raises(ValueError, match="symlink"):
        _reject_allowed_symlinks(contract, snapshot)


def test_recovery_preserves_original_unicode_spelling(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
    path = "Cafe\u0301/file"
    target = repo / path
    target.parent.mkdir()
    target.write_bytes(b"original")
    contract = TaskContract("Task", ("Caf\u00e9",), (), ("verified",))
    snapshot = capture_patch(repo, contract, "attempt", (path,))
    assert snapshot.images[0].path == path
    assert snapshot.images[0].data == b"original"


def test_recovery_rejects_unicode_duplicate_targets(tmp_path: Path) -> None:
    contract = TaskContract("Task", ("Caf\u00e9",), (), ("verified",))
    with pytest.raises(ValueError, match="identity or paths"):
        capture_patch(
            tmp_path / "absent", contract, "attempt", ("Caf\u00e9", "Cafe\u0301")
        )


def test_recovery_delta_covers_unicode_equivalent_image_identity(
    tmp_path: Path,
) -> None:
    from agent_harness.recovery import FileImage, PatchSnapshot, _complete_delta

    observed_path = "Cafe\u0301/file"
    before = observation(tmp_path)
    after = replace(
        before,
        files=(FileFingerprint(observed_path, "regular", "1" * 64, 1, False),),
    )
    old = PatchSnapshot(
        before, "0" * 64, "attempt", (FileImage("Caf\u00e9/file", None, None),)
    )
    new = replace(old, observation=after)
    _complete_delta(old, new)


@pytest.mark.parametrize("consumer", ["seal", "load"])
def test_recovery_readers_reject_unicode_duplicate_images(
    tmp_path: Path, consumer: str
) -> None:
    from agent_harness.recovery import (
        FileImage,
        PatchSnapshot,
        _digest,
        _encoded,
        _load_snapshot,
        seal_patch,
    )

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
    contract = TaskContract("Task", ("Caf\u00e9",), (), ("verified",))
    snapshot = PatchSnapshot(
        RepositoryObservation.capture(repo),
        _digest(contract),
        "attempt",
        (FileImage("Caf\u00e9", None, None), FileImage("Cafe\u0301", None, None)),
    )
    message = (
        "duplicate recovery image" if consumer == "seal" else "invalid recovery images"
    )
    with pytest.raises(ValueError, match=message):
        if consumer == "seal":
            seal_patch(snapshot, snapshot, tmp_path / "artifact")
        else:
            _load_snapshot(_encoded(snapshot), repo.resolve(), contract, "attempt")


@pytest.mark.parametrize("inventory", ["files", "file_and_status", "statuses"])
def test_observation_rejects_distinct_unicode_aliases(
    tmp_path: Path, inventory: str
) -> None:
    from agent_harness.scope import _validate_observation
    from agent_harness.turn import GitStatusEntry

    paths = ("Caf\u00e9/file", "Cafe\u0301/file")
    files = tuple(
        FileFingerprint(path, "regular", "1" * 64, 1, False) for path in paths
    )
    if inventory == "files":
        snapshot = replace(observation(tmp_path), files=files)
    elif inventory == "file_and_status":
        snapshot = replace(
            observation(tmp_path),
            files=files[:1],
            status=(GitStatusEntry(paths[1], "D "),),
        )
    else:
        snapshot = replace(
            observation(tmp_path),
            status=tuple(GitStatusEntry(path, "D ") for path in paths),
        )
    with pytest.raises(ValueError, match="invalid scope observation"):
        _validate_observation(snapshot)


@pytest.mark.parametrize("consumer", ["seal", "load"])
def test_recovery_rejects_ambiguous_unicode_observation_before_persistence(
    tmp_path: Path, consumer: str
) -> None:
    from agent_harness.recovery import (
        FileImage,
        PatchSnapshot,
        _digest,
        _encoded,
        _load_snapshot,
        seal_patch,
    )

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=10)
    data = b"before"
    paths = ("Caf\u00e9/file", "Cafe\u0301/file")
    observed = replace(
        RepositoryObservation.capture(repo),
        files=tuple(
            FileFingerprint(
                path,
                "regular",
                hashlib.sha256(data).hexdigest(),
                len(data),
                False,
                0o644,
            )
            for path in paths
        ),
    )
    contract = TaskContract("Task", ("Caf\u00e9",), (), ("verified",))
    snapshot = PatchSnapshot(
        observed, _digest(contract), "attempt", (FileImage(paths[0], data, 0o644),)
    )
    artifact = tmp_path / "artifact"
    with pytest.raises(ValueError, match="invalid scope observation"):
        if consumer == "seal":
            seal_patch(snapshot, snapshot, artifact)
        else:
            _load_snapshot(
                json.loads(json.dumps(_encoded(snapshot))),
                repo.resolve(),
                contract,
                "attempt",
            )
    assert not artifact.exists()

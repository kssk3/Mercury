from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_harness.contract import TaskContract


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, timeout=10
    ).stdout


@pytest.fixture(scope="module")
def repository_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = (tmp_path_factory.mktemp("recovery-repository") / "repo").resolve()
    root.mkdir()
    git(root, "init", "-q")
    # A copied seed must not outlive a detached Git housekeeping writer.
    git(root, "config", "--local", "maintenance.auto", "false")
    (root / "modify").write_bytes(b"base")
    (root / "delete").write_bytes(b"delete")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@invalid",
        "commit",
        "-qm",
        "base",
    )
    return root


@pytest.fixture
def repo(tmp_path: Path, repository_seed: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    shutil.copytree(repository_seed, root)
    return root


def contract() -> TaskContract:
    return TaskContract("restore", ("modify", "delete", "create"), (), ("exact",))


def test_exact_delta_preserves_dirty_staged_baseline(
    repo: Path, tmp_path: Path
) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    (repo / "modify").write_bytes(b"staged")
    git(repo, "add", "modify")
    (repo / "modify").write_bytes(b"\xff\x00dirty")
    (repo / "modify").chmod(0o751)
    before = capture_patch(repo, contract(), "attempt", ("modify", "delete", "create"))
    (repo / "modify").write_bytes(b"changed")
    (repo / "modify").chmod(0o600)
    (repo / "delete").unlink()
    (repo / "create").write_bytes(b"")
    after = capture_patch(repo, contract(), "attempt", ("modify", "delete", "create"))
    reference = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert reference.path.stat().st_mode & 0o777 == 0o600
    assert recover_patch(repo, contract(), "attempt", reference).status == "restored"
    assert (repo / "modify").read_bytes() == b"\xff\x00dirty"
    assert (repo / "modify").stat().st_mode & 0o777 == 0o751
    assert (repo / "delete").read_bytes() == b"delete"
    assert not (repo / "create").exists()
    assert git(repo, "show", ":modify") == b"staged"
    assert (
        recover_patch(repo, contract(), "attempt", reference).status
        == "already_restored"
    )


def test_all_target_conflict_never_writes(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify", "delete"))
    (repo / "modify").write_bytes(b"agent")
    (repo / "delete").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "delete"))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    (repo / "delete").write_bytes(b"user later")
    with pytest.raises(ValueError, match="conflict"):
        recover_patch(repo, contract(), "a", ref)
    assert (repo / "modify").read_bytes() == b"agent"
    assert (repo / "delete").read_bytes() == b"user later"


@pytest.mark.parametrize(
    "unsafe", ["../modify", "/modify", "./modify", ".git/index", "modify//x"]
)
def test_unsafe_paths_are_rejected(repo: Path, unsafe: str) -> None:
    from agent_harness.recovery import capture_patch

    with pytest.raises(ValueError):
        capture_patch(repo, contract(), "a", (unsafe,))


def test_hardlinks_and_setid_modes_rejected(repo: Path) -> None:
    from agent_harness.recovery import capture_patch

    os.link(repo / "modify", repo / "alias")
    with pytest.raises(ValueError):
        capture_patch(repo, contract(), "a", ("modify",))
    (repo / "alias").unlink()
    (repo / "modify").chmod(0o4755)
    with pytest.raises(ValueError):
        capture_patch(repo, contract(), "a", ("modify",))


def test_snapshot_forgery_cannot_seal(repo: Path, tmp_path: Path) -> None:
    from dataclasses import replace

    from agent_harness.recovery import FileImage, capture_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    forged = replace(before, images=(FileImage("modify", b"forged", 0o644),))
    with pytest.raises(ValueError):
        seal_patch(forged, before, tmp_path.resolve() / "artifact")


def test_nested_deleted_file_recreates_safe_parent(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    policy = TaskContract("restore", ("nested",), (), ("exact",))
    (repo / "nested").mkdir()
    (repo / "nested/file").write_bytes(b"original")
    before = capture_patch(repo, policy, "a", ("nested/file",))
    (repo / "nested/file").unlink()
    (repo / "nested").rmdir()
    after = capture_patch(repo, policy, "a", ("nested/file",))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert recover_patch(repo, policy, "a", ref).status == "restored"
    assert (repo / "nested/file").read_bytes() == b"original"


def test_partial_io_reports_unknown_and_retains_artifact(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_harness.recovery as recovery

    before = recovery.capture_patch(repo, contract(), "a", ("modify", "delete"))
    (repo / "modify").write_bytes(b"agent")
    (repo / "delete").write_bytes(b"agent")
    after = recovery.capture_patch(repo, contract(), "a", ("modify", "delete"))
    ref = recovery.seal_patch(before, after, tmp_path.resolve() / "artifact")
    original = os.replace
    calls = 0

    def fail_second(source: str, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private failure payload")
        original(source, destination)

    monkeypatch.setattr(os, "replace", fail_second)
    result = recovery.recover_patch(repo, contract(), "a", ref)
    assert result.status == "partial_unknown"
    assert (repo / "modify").read_bytes() == b"base"
    assert (repo / "delete").read_bytes() == b"agent"
    assert ref.path.exists()


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "constant", "bool", "fingerprint", "image", "extra_observation"],
)
def test_malformed_trusted_artifact_refused(
    repo: Path, tmp_path: Path, mutation: str
) -> None:
    import hashlib
    import json
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    raw = json.loads(ref.path.read_bytes())
    if mutation == "bool":
        raw["version"] = True
    elif mutation == "fingerprint":
        raw["before"]["observation"]["files"][0]["size_bytes"] = True
    elif mutation == "image":
        raw["before"]["images"][0]["data"] = "Zm9yZ2Vk"
    elif mutation == "extra_observation":
        raw["before"]["observation"]["surprise"] = "extra"
    payload = json.dumps(raw).encode()
    if mutation == "duplicate":
        payload = payload.replace(b'"version": 1', b'"version": 1, "version": 1')
    elif mutation == "constant":
        payload = payload.replace(b'"version": 1', b'"version": NaN')
    ref.path.write_bytes(payload)
    trusted = replace(ref, sha256=hashlib.sha256(payload).hexdigest())
    with pytest.raises(ValueError):
        recover_patch(repo, contract(), "a", trusted)
    assert (repo / "modify").read_bytes() == b"agent"


def test_identity_and_tamper_refused(repo: Path, tmp_path: Path) -> None:
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    for foreign in (
        replace(ref, attempt_id="other"),
        replace(ref, repository_id="0" * 64),
        replace(ref, contract_sha256="0" * 64),
    ):
        with pytest.raises(ValueError):
            recover_patch(repo, contract(), "a", foreign)
    ref.path.write_bytes(ref.path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        recover_patch(repo, contract(), "a", ref)
    assert (repo / "modify").read_bytes() == b"agent"


def test_budget_refused_without_truncation(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_harness.recovery as recovery

    monkeypatch.setattr(recovery, "MAX_BYTES", 2)
    with pytest.raises(ValueError, match="budget"):
        recovery.capture_patch(repo, contract(), "a", ("modify",))
    assert (repo / "modify").read_bytes() == b"base"


def test_incomplete_delta_cannot_seal(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    (repo / "delete").write_bytes(b"unrecorded agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    with pytest.raises(ValueError, match="incomplete"):
        seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert not (tmp_path / "artifact").exists()


def test_symlink_target_and_ancestor_refused(repo: Path) -> None:
    from agent_harness.recovery import capture_patch

    (repo / "modify").unlink()
    (repo / "modify").symlink_to("delete")
    with pytest.raises(ValueError):
        capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").unlink()
    (repo / "modify").symlink_to(repo)
    policy = TaskContract("restore", ("modify",), (), ("exact",))
    with pytest.raises(ValueError):
        capture_patch(repo, policy, "a", ("modify/delete",))


def test_external_artifact_rejects_git_metadata_and_symlinks(
    repo: Path, tmp_path: Path
) -> None:
    from agent_harness.recovery import capture_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    for target in (repo / "artifact", repo / ".git/artifact"):
        with pytest.raises(ValueError):
            seal_patch(before, after, target)
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        seal_patch(before, after, tmp_path / "link/artifact")


def test_unrelated_later_change_refuses_recovery(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    (repo / "delete").write_bytes(b"later user")
    with pytest.raises(ValueError, match="conflict"):
        recover_patch(repo, contract(), "a", ref)
    assert (repo / "modify").read_bytes() == b"agent"
    assert (repo / "delete").read_bytes() == b"later user"


def test_interruption_reports_unknown(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_harness.recovery as recovery

    before = recovery.capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = recovery.capture_patch(repo, contract(), "a", ("modify",))
    ref = recovery.seal_patch(before, after, tmp_path.resolve() / "artifact")

    def interrupt(source: str, destination: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    assert (
        recovery.recover_patch(repo, contract(), "a", ref).status == "partial_unknown"
    )
    assert ref.path.exists()
    assert (repo / "modify").read_bytes() == b"agent"


def test_allowed_scope_is_literal_case_sensitive(repo: Path) -> None:
    from agent_harness.recovery import capture_patch

    (repo / "SRC").mkdir()
    (repo / "SRC/file").write_bytes(b"private outside")
    policy = TaskContract("restore", ("src",), (), ("exact",))
    with pytest.raises(ValueError, match="scope"):
        capture_patch(repo, policy, "a", ("SRC/file",))
    assert (repo / "SRC/file").read_bytes() == b"private outside"


def test_cached_deletion_baseline_preserved(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import RepositoryObservation

    git(repo, "rm", "--cached", "--quiet", "delete")
    original = RepositoryObservation.capture(repo)
    assert {entry.status for entry in original.status if entry.path == "delete"} == {
        "D ",
        "??",
    }
    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    reference = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert recover_patch(repo, contract(), "a", reference).status == "restored"
    assert RepositoryObservation.capture(repo) == original
    assert (repo / "delete").read_bytes() == b"delete"


@pytest.mark.parametrize(
    "statuses", [(" M", " M"), (" M", "??"), ("D ", "!!"), ("XX",)]
)
def test_invalid_status_pairs_remain_rejected(
    repo: Path, tmp_path: Path, statuses: tuple[str, ...]
) -> None:
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, seal_patch
    from agent_harness.turn import GitStatusEntry

    before = capture_patch(repo, contract(), "a", ("modify",))
    invalid = replace(
        before,
        observation=replace(
            before.observation,
            status=tuple(GitStatusEntry("modify", value) for value in statuses),
        ),
    )
    with pytest.raises(ValueError):
        seal_patch(invalid, before, tmp_path.resolve() / "artifact")


def test_missing_file_untracked_status_cannot_seal(repo: Path, tmp_path: Path) -> None:
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, seal_patch
    from agent_harness.turn import GitStatusEntry

    before = capture_patch(repo, contract(), "a", ("modify", "create"))
    forged = replace(
        before,
        observation=replace(
            before.observation, status=(GitStatusEntry("create", "??"),)
        ),
    )
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "create"))
    with pytest.raises(ValueError):
        seal_patch(forged, after, tmp_path.resolve() / "artifact")
    assert not (tmp_path / "artifact").exists()
    assert (repo / "modify").read_bytes() == b"agent"


def test_missing_file_untracked_status_refuses_before_first_write(
    repo: Path, tmp_path: Path
) -> None:
    import hashlib
    import json
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "a", ("modify", "create"))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "create"))
    reference = seal_patch(before, after, tmp_path.resolve() / "artifact")
    payload = json.loads(reference.path.read_bytes())
    payload["before"]["observation"]["status"] = [{"path": "create", "status": "??"}]
    raw = json.dumps(payload).encode()
    reference.path.write_bytes(raw)
    trusted = replace(reference, sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        recover_patch(repo, contract(), "a", trusted)
    assert (repo / "modify").read_bytes() == b"agent"
    assert not (repo / "create").exists()


@pytest.mark.parametrize(
    "mutation", ["missing_untracked", "missing_inventory", "deleted_present"]
)
@pytest.mark.parametrize("boundary", ["seal", "recover"])
def test_presence_contradictions_refused_before_writes(
    repo: Path, tmp_path: Path, mutation: str, boundary: str
) -> None:
    import hashlib
    import json
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import FileFingerprint, GitStatusEntry

    before = capture_patch(repo, contract(), "a", ("modify", "create"))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "create"))
    if mutation == "deleted_present":
        forged = replace(
            before,
            observation=replace(
                before.observation, status=(GitStatusEntry("modify", " D"),)
            ),
        )
    else:
        status = (
            (GitStatusEntry("create", "??"),) if mutation == "missing_untracked" else ()
        )
        forged = replace(
            before,
            observation=replace(
                before.observation,
                files=before.observation.files
                + (FileFingerprint("create", "missing", None, None, None),),
                status=status,
            ),
        )
    if boundary == "seal":
        with pytest.raises(ValueError):
            seal_patch(forged, after, tmp_path.resolve() / "artifact")
        assert not (tmp_path / "artifact").exists()
    else:
        ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
        payload = json.loads(ref.path.read_bytes())
        if mutation == "deleted_present":
            payload["before"]["observation"]["status"] = [
                {"path": "modify", "status": " D"}
            ]
        else:
            payload["before"]["observation"]["files"].append(
                {
                    "path": "create",
                    "kind": "missing",
                    "sha256": None,
                    "size_bytes": None,
                    "executable": None,
                }
            )
            payload["before"]["observation"]["status"] = (
                [{"path": "create", "status": "??"}]
                if mutation == "missing_untracked"
                else []
            )
        raw = json.dumps(payload).encode()
        ref.path.write_bytes(raw)
        trusted = replace(ref, sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError):
            recover_patch(repo, contract(), "a", trusted)
    assert (repo / "modify").read_bytes() == b"agent"
    assert not (repo / "create").exists()


def test_preexisting_tracked_missing_file_preserved(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import RepositoryObservation

    (repo / "delete").unlink()
    original = RepositoryObservation.capture(repo)
    before = capture_patch(repo, contract(), "a", ("modify", "delete"))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "delete"))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert recover_patch(repo, contract(), "a", ref).status == "restored"
    assert RepositoryObservation.capture(repo) == original
    assert not (repo / "delete").exists()


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_index_flags_do_not_reject_tracked_missing_baseline(
    repo: Path, tmp_path: Path, flag: str
) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import RepositoryObservation

    git(repo, "update-index", flag, "delete")
    (repo / "delete").unlink()
    original = RepositoryObservation.capture(repo)
    flags = git(repo, "ls-files", "-v")
    before = capture_patch(repo, contract(), "a", ("modify", "delete"))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "delete"))
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert recover_patch(repo, contract(), "a", ref).status == "restored"
    assert RepositoryObservation.capture(repo) == original
    assert git(repo, "ls-files", "-v") == flags
    assert not (repo / "delete").exists()


@pytest.mark.parametrize(
    "mutation", ["omit_present", "omit_tracked_missing", "fake_staged"]
)
@pytest.mark.parametrize("boundary", ["seal", "recover"])
def test_incomplete_inventory_or_changed_stage_refused_before_writes(
    repo: Path, tmp_path: Path, mutation: str, boundary: str
) -> None:
    import hashlib
    import json
    from dataclasses import replace

    from agent_harness.recovery import (
        FileImage,
        capture_patch,
        recover_patch,
        seal_patch,
    )
    from agent_harness.turn import GitStatusEntry, RepositoryObservation

    if mutation == "omit_tracked_missing":
        (repo / "delete").unlink()
    before = capture_patch(repo, contract(), "a", ("modify", "delete"))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify", "delete"))
    untouched = RepositoryObservation.capture(repo)
    if mutation == "fake_staged":
        forged = replace(
            before,
            observation=replace(
                before.observation, status=(GitStatusEntry("modify", "M "),)
            ),
        )
    else:
        forged = replace(
            before,
            observation=replace(
                before.observation,
                files=tuple(
                    entry
                    for entry in before.observation.files
                    if entry.path != "delete"
                ),
            ),
            images=tuple(
                FileImage("delete", None, None) if image.path == "delete" else image
                for image in before.images
            ),
        )
    if boundary == "seal":
        with pytest.raises(ValueError):
            seal_patch(forged, after, tmp_path.resolve() / "artifact")
        assert not (tmp_path / "artifact").exists()
    else:
        ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
        payload = json.loads(ref.path.read_bytes())
        if mutation == "fake_staged":
            payload["before"]["observation"]["status"] = [
                {"path": "modify", "status": "M "}
            ]
        else:
            payload["before"]["observation"]["files"] = [
                entry
                for entry in payload["before"]["observation"]["files"]
                if entry["path"] != "delete"
            ]
            for image in payload["before"]["images"]:
                if image["path"] == "delete":
                    image["data"], image["mode"] = None, None
        raw = json.dumps(payload).encode()
        ref.path.write_bytes(raw)
        trusted = replace(ref, sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError):
            recover_patch(repo, contract(), "a", trusted)
    assert RepositoryObservation.capture(repo) == untouched
    assert (repo / "modify").read_bytes() == b"agent"
    if mutation != "omit_tracked_missing":
        assert (repo / "delete").read_bytes() == b"delete"


@pytest.mark.parametrize("modes", [(0o600, 0o666), (0o755, 0o777)])
def test_flagged_permission_only_patch_restores_exact_snapshot(
    repo: Path, tmp_path: Path, modes: tuple[int, int]
) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import RepositoryObservation

    name = "tab\tnewline\n한글"
    (repo / name).write_bytes(b"unchanged")
    git(repo, "add", "--", name)
    git(repo, "update-index", "--skip-worktree", "--", name)
    git(repo, "update-index", "--assume-unchanged", "--", name)
    path = repo / "modify"
    path.chmod(modes[0])
    before = capture_patch(repo, contract(), "a", ("modify",))
    path.chmod(modes[1])
    after = capture_patch(repo, contract(), "a", ("modify",))
    assert before.observation.status == after.observation.status
    ref = seal_patch(before, after, tmp_path.resolve() / "artifact")
    assert recover_patch(repo, contract(), "a", ref).status == "restored"
    assert RepositoryObservation.capture(repo) == before.observation
    assert path.stat().st_mode & 0o777 == modes[0]


def test_capture_rejects_image_permission_disagreement(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    import agent_harness.recovery as recovery

    (repo / "modify").chmod(0o600)
    original = recovery._image

    def mismatched(root: Path, relative: str) -> recovery.FileImage:
        return replace(original(root, relative), mode=0o666)

    monkeypatch.setattr(recovery, "_image", mismatched)
    with pytest.raises(ValueError, match="observation disagreement"):
        recovery.capture_patch(repo, contract(), "a", ("modify",))
    assert (repo / "modify").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("boundary", ["seal", "recover"])
def test_image_permission_disagreement_refused_before_writes(
    repo: Path, tmp_path: Path, boundary: str
) -> None:
    import hashlib
    import json
    from dataclasses import replace

    from agent_harness.recovery import capture_patch, recover_patch, seal_patch
    from agent_harness.turn import RepositoryObservation

    (repo / "modify").chmod(0o600)
    before = capture_patch(repo, contract(), "a", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "a", ("modify",))
    untouched = RepositoryObservation.capture(repo)
    artifact = tmp_path.resolve() / "artifact"
    if boundary == "seal":
        forged = replace(before, images=(replace(before.images[0], mode=0o666),))
        with pytest.raises(ValueError, match="observation disagreement"):
            seal_patch(forged, after, artifact)
        assert not artifact.exists()
    else:
        ref = seal_patch(before, after, artifact)
        payload = json.loads(artifact.read_bytes())
        payload["before"]["images"][0]["mode"] = 0o666
        raw = json.dumps(payload).encode()
        artifact.write_bytes(raw)
        trusted = replace(ref, sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError, match="observation disagreement"):
            recover_patch(repo, contract(), "a", trusted)
    assert RepositoryObservation.capture(repo) == untouched


def test_recovery_rejects_same_commit_branch_switch(repo: Path, tmp_path: Path) -> None:
    from agent_harness.recovery import capture_patch, recover_patch, seal_patch

    before = capture_patch(repo, contract(), "branch", ("modify",))
    (repo / "modify").write_bytes(b"agent")
    after = capture_patch(repo, contract(), "branch", ("modify",))
    reference = seal_patch(before, after, tmp_path.resolve() / "artifact")
    git(repo, "switch", "-c", "other")
    with pytest.raises(ValueError, match="workspace conflict"):
        recover_patch(repo, contract(), "branch", reference)
    assert (repo / "modify").read_bytes() == b"agent"


def test_recovery_seal_rejects_same_commit_head_identity_change(
    repo: Path, tmp_path: Path
) -> None:
    from agent_harness.recovery import capture_patch, seal_patch

    before = capture_patch(repo, contract(), "branch", ("modify",))
    git(repo, "switch", "--detach")
    after = capture_patch(repo, contract(), "branch", ("modify",))
    with pytest.raises(ValueError, match="incompatible"):
        seal_patch(before, after, tmp_path.resolve() / "artifact")

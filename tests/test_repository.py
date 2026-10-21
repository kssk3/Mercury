from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness import repository as repository_module
from agent_harness.repository import resolve_repository_state_location


def initialize_repository(path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def test_resolver_maps_root_and_child_to_one_external_state_location(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    child = repository / "nested"
    child.mkdir()
    state_root = tmp_path / "harness-state"

    root_location = resolve_repository_state_location(repository, state_root)
    child_location = resolve_repository_state_location(child, state_root)

    assert child_location == root_location
    assert root_location.repository_root == repository.resolve()
    assert (
        root_location.repository_id
        == hashlib.sha256(str(repository.resolve()).encode()).hexdigest()
    )
    assert root_location.state_directory == (
        state_root.resolve() / "repositories" / root_location.repository_id
    )
    assert not root_location.state_directory.exists()


def test_resolver_rejects_a_state_root_inside_the_repository(tmp_path: Path) -> None:
    repository = initialize_repository(tmp_path / "repository")

    with pytest.raises(ValueError, match="outside the repository"):
        resolve_repository_state_location(repository, repository / ".agent-harness")


@pytest.mark.parametrize("boundary", ["repository", "metadata"])
@pytest.mark.parametrize("consumer", ["explicit", "resolver"])
def test_runtime_state_case_alias_boundary(
    tmp_path: Path, boundary: str, consumer: str
) -> None:
    root = tmp_path / "Project"
    if boundary == "metadata":
        protected = tmp_path / "Metadata"
        subprocess.run(
            ["git", "init", "--quiet", "--separate-git-dir", str(protected), str(root)],
            check=True,
            timeout=10,
        )
    else:
        protected = initialize_repository(root)
    alias = protected.with_name(protected.name.lower())
    same_directory = alias.exists() and alias.samefile(protected)
    if not same_directory:
        alias.mkdir()
    state = alias / "new-state"

    def validate() -> None:
        if consumer == "explicit":
            repository_module.validate_runtime_state_directory(state, root)
        else:
            resolve_repository_state_location(root, state)

    if same_directory:
        with pytest.raises(ValueError, match="outside"):
            validate()
    else:
        # Distinct directories on a case-sensitive filesystem are safe.
        validate()
    assert not state.exists()


def test_resolver_rejects_in_repository_state_root_redirected_outside(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    state_root = repository / ".agent-harness"
    state_root.mkdir()
    external_repositories = tmp_path / "external-repositories"
    external_repositories.mkdir()
    (state_root / "repositories").symlink_to(
        external_repositories, target_is_directory=True
    )

    with pytest.raises(ValueError, match="outside the repository"):
        resolve_repository_state_location(repository, state_root)

    assert not any(external_repositories.iterdir())


def test_resolver_rejects_state_directory_redirected_inside_repository(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    redirected_directory = repository / "redirected-state"
    redirected_directory.mkdir()
    state_root = tmp_path / "harness-state"
    state_root.mkdir()
    (state_root / "repositories").symlink_to(
        redirected_directory, target_is_directory=True
    )

    with pytest.raises(ValueError, match="outside the repository"):
        resolve_repository_state_location(repository, state_root)


def test_resolver_ignores_inherited_repository_selection_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_repository = initialize_repository(tmp_path / "requested")
    unrelated_repository = initialize_repository(tmp_path / "unrelated")
    monkeypatch.setenv("GIT_DIR", str(unrelated_repository / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated_repository))

    location = resolve_repository_state_location(
        requested_repository,
        tmp_path / "harness-state",
    )

    assert location.repository_root == requested_repository.resolve()


def test_resolver_maps_existing_file_like_its_parent_directory(tmp_path: Path) -> None:
    repository = initialize_repository(tmp_path / "repository")
    repository_file = repository / "example.py"
    repository_file.write_text("print('example')\n", encoding="utf-8")
    state_root = tmp_path / "harness-state"

    file_location = resolve_repository_state_location(repository_file, state_root)
    parent_location = resolve_repository_state_location(
        repository_file.parent, state_root
    )

    assert file_location == parent_location


def test_resolver_maps_in_worktree_symlink_entry_even_when_target_is_outside(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    external_target = tmp_path / "external.txt"
    external_target.write_text("external\n", encoding="utf-8")
    repository_symlink = repository / "external-link"
    repository_symlink.symlink_to(external_target)
    state_root = tmp_path / "harness-state"

    symlink_location = resolve_repository_state_location(
        repository_symlink,
        state_root,
    )
    root_location = resolve_repository_state_location(repository, state_root)

    assert symlink_location == root_location


def test_resolver_uses_the_admitted_macos_default_without_creating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    location = resolve_repository_state_location(repository)

    assert location.state_directory == (
        home
        / "Library"
        / "Application Support"
        / "agent-harness"
        / "repositories"
        / location.repository_id
    )
    assert not location.state_directory.exists()


def test_public_root_resolver_ignores_inherited_git_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = initialize_repository(tmp_path / "requested")
    other = initialize_repository(tmp_path / "other")
    child = repository / "nested"
    child.mkdir()
    entry = child / "source.py"
    entry.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(other / ".git" / "objects"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repository))

    assert repository_module.resolve_repository_root(repository) == repository.resolve()
    assert repository_module.resolve_repository_root(child) == repository.resolve()
    assert repository_module.resolve_repository_root(entry) == repository.resolve()


def test_public_root_resolver_rejects_non_git_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="Git working tree"):
        repository_module.resolve_repository_root(outside)


def test_public_root_resolver_preserves_trailing_space_in_root(tmp_path: Path) -> None:
    repository = initialize_repository(tmp_path / "repository ")

    assert repository_module.resolve_repository_root(repository) == repository.resolve()


@pytest.mark.parametrize("component", ["repositories", "repository-id"])
@pytest.mark.parametrize("dangling", [False, True], ids=["existing", "dangling"])
def test_resolver_rejects_composed_state_symlinks_without_following(
    tmp_path: Path, component: str, dangling: bool
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    state_root = tmp_path / "state"
    location = resolve_repository_state_location(repository, state_root)
    redirected = tmp_path / "redirected"
    if not dangling:
        redirected.mkdir()
        (redirected / "preserved.txt").write_bytes(b"original state bytes")
    link = (
        state_root / "repositories"
        if component == "repositories"
        else location.state_directory
    )
    link.parent.mkdir(parents=True)
    link.symlink_to(redirected, target_is_directory=True)
    original_entries = set(state_root.rglob("*"))

    with pytest.raises(ValueError):
        resolve_repository_state_location(repository, state_root)

    assert link.is_symlink()
    assert set(state_root.rglob("*")) == original_entries
    if dangling:
        assert not redirected.exists()
    else:
        assert set(redirected.iterdir()) == {redirected / "preserved.txt"}
        assert (redirected / "preserved.txt").read_bytes() == b"original state bytes"


def test_resolver_preserves_caller_state_root_symlink_canonicalization(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(tmp_path / "repository")
    state_root = tmp_path / "state"
    state_root.mkdir()
    alias = tmp_path / "state-alias"
    alias.symlink_to(state_root, target_is_directory=True)

    location = resolve_repository_state_location(repository, alias)

    assert location.state_directory == (
        state_root.resolve() / "repositories" / location.repository_id
    )
    assert not location.state_directory.exists()


def test_internal_git_ignores_repo_local_path_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.contract import TaskContract
    from agent_harness.profile import build_repository_profile
    from agent_harness.recovery import capture_patch
    from agent_harness.turn import RepositoryObservation
    from agent_harness.workspace import WorkspaceSnapshot

    root = initialize_repository(tmp_path / "repo")
    (root / "file").write_text("original")
    tools = root / "tools"
    tools.mkdir()
    marker = tmp_path / "executed"
    shadow = tools / "git"
    shadow.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    shadow.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    assert repository_module.resolve_repository_root(root) == root.resolve()
    RepositoryObservation.capture(root)
    WorkspaceSnapshot.capture(root)
    build_repository_profile(root)
    capture_patch(
        root, TaskContract("capture", ("file",), (), ("done",)), "attempt", ("file",)
    )
    assert not marker.exists()


@pytest.mark.parametrize("layout", ["linked", "separate"])
@pytest.mark.parametrize("metadata", ["gitdir", "common"])
@pytest.mark.parametrize(
    "consumer", ["resolver", "journal", "state", "output", "lock", "turn"]
)
def test_runtime_state_rejects_linked_git_metadata(
    tmp_path: Path, layout: str, metadata: str, consumer: str
) -> None:
    from agent_harness.journal import EventJournal
    from agent_harness.lock import RepositoryLock
    from agent_harness.output import OutputStore
    from agent_harness.state import CurrentStateStore
    from agent_harness.turn import _validate_journal

    root = tmp_path / "repo"
    if layout == "linked":
        primary = initialize_repository(tmp_path / "primary")
        subprocess.run(
            [
                "git",
                "-C",
                str(primary),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "base",
            ],
            check=True,
            timeout=10,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(primary),
                "worktree",
                "add",
                "--quiet",
                "-b",
                "linked",
                str(root),
            ],
            check=True,
            timeout=10,
        )
    else:
        subprocess.run(
            [
                "git",
                "init",
                "--quiet",
                "--separate-git-dir",
                str(tmp_path / "metadata"),
                str(root),
            ],
            check=True,
            timeout=10,
        )
    selector = "--absolute-git-dir" if metadata == "gitdir" else "--git-common-dir"
    raw = subprocess.run(
        ["git", "-C", str(root), "rev-parse", selector],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.removesuffix("\n")
    gitdir = Path(raw) if Path(raw).is_absolute() else root / raw
    state = gitdir.resolve() / "runtime"
    with pytest.raises(ValueError):
        if consumer == "resolver":
            resolve_repository_state_location(root, state)
        elif consumer == "journal":
            EventJournal(state).append({"event": "refused"})
        elif consumer == "state":
            CurrentStateStore(state).write({"refused": True})
        elif consumer == "output":
            OutputStore(state, max_bytes=128, redactor=lambda text: text).write(
                "refused"
            )
        elif consumer == "lock":
            RepositoryLock(state, timeout_seconds=10).acquire()
        else:
            _validate_journal(EventJournal(state), root)
    assert not state.exists()


def test_journal_initial_metadata_query_failure_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.journal import EventJournal
    from agent_harness.turn import _validate_journal

    root = initialize_repository(tmp_path / "repo")
    journal = EventJournal(tmp_path / "state")

    def failed(*args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 128, "", "missing")

    monkeypatch.setattr(repository_module, "_git_query", failed)
    with pytest.raises(ValueError, match="could not inspect"):
        _validate_journal(journal, root, bind=True)
    assert journal._git_metadata_binding is None
    assert not journal.path.parent.exists()


def test_journal_bound_metadata_rejects_unsafe_fallback(tmp_path: Path) -> None:
    from agent_harness.journal import EventJournal
    from agent_harness.turn import _validate_journal

    root = tmp_path / "repo"
    metadata = tmp_path / "metadata"
    subprocess.run(
        ["git", "init", "--quiet", "--separate-git-dir", str(metadata), str(root)],
        check=True,
        timeout=10,
    )
    journal = EventJournal(tmp_path / "state")
    _validate_journal(journal, root, bind=True)
    metadata.rename(tmp_path / "removed")
    journal._state_directory = metadata / "runtime"
    with pytest.raises(ValueError, match="Git metadata"):
        _validate_journal(journal, root)
    assert not journal.path.parent.exists()


def test_journal_live_unsafe_metadata_cannot_use_old_binding(tmp_path: Path) -> None:
    from agent_harness.journal import EventJournal
    from agent_harness.turn import _validate_journal

    root = initialize_repository(tmp_path / "repo")
    state = tmp_path / "state"
    journal = EventJournal(state)
    _validate_journal(journal, root, bind=True)
    state.mkdir()
    new_metadata = state / "metadata"
    root.joinpath(".git").rename(tmp_path / "old-metadata")
    subprocess.run(
        ["git", "init", "--quiet", "--separate-git-dir", str(new_metadata), str(root)],
        check=True,
        timeout=10,
    )
    journal._state_directory = new_metadata / "runtime"
    with pytest.raises(ValueError, match="Git metadata"):
        _validate_journal(journal, root)
    assert not journal.path.parent.exists()


def test_runtime_state_unexpected_git_query_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.state import CurrentStateStore

    def failed(*args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [], 128, "", "fatal: detected dubious ownership in repository"
        )

    monkeypatch.setattr(repository_module, "_git_query", failed)
    state = tmp_path / "state"
    with pytest.raises(ValueError, match="could not inspect"):
        CurrentStateStore(state).write({"refused": True})
    assert not state.exists()

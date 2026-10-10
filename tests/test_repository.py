from __future__ import annotations

import hashlib
import subprocess
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

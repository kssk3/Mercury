from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_harness.repository import git_read_environment
from agent_harness.workspace import WorkspaceSnapshot


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True)


def test_snapshot_separates_staged_unstaged_and_untracked_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    git(tmp_path, "init", "--quiet", str(repository))
    (repository / "staged.txt").write_text("base")
    (repository / "unstaged.txt").write_text("base")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "base",
    )
    (repository / "staged.txt").write_text("changed")
    git(repository, "add", "staged.txt")
    (repository / "unstaged.txt").write_text("changed")
    (repository / "new.txt").write_text("new")

    snapshot = WorkspaceSnapshot.capture(repository)

    assert snapshot.staged_paths == ("staged.txt",)
    assert snapshot.unstaged_paths == ("unstaged.txt",)
    assert snapshot.untracked_paths == ("new.txt",)


def test_snapshot_preserves_special_and_nested_path_names(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    git(tmp_path, "init", "--quiet", str(repository))
    staged_path = "staged\nname.txt"
    unstaged_path = "unstaged\rname.txt"
    (repository / staged_path).write_text("base")
    (repository / unstaged_path).write_text("base")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "base",
    )
    (repository / staged_path).write_text("changed")
    git(repository, "add", staged_path)
    (repository / unstaged_path).write_text("changed")
    untracked_path = "nested/untracked\r\nname.txt"
    (repository / untracked_path).parent.mkdir()
    (repository / untracked_path).write_text("new")

    snapshot = WorkspaceSnapshot.capture(repository)

    assert snapshot.staged_paths == (staged_path,)
    assert snapshot.unstaged_paths == (unstaged_path,)
    assert snapshot.untracked_paths == (untracked_path,)


def test_snapshot_includes_both_paths_for_a_staged_rename(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    git(tmp_path, "init", "--quiet", str(repository))
    source_path = "before\nname.txt"
    destination_path = "after\rname.txt"
    (repository / source_path).write_text("base")
    git(repository, "add", source_path)
    git(
        repository,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "base",
    )
    git(repository, "mv", source_path, destination_path)

    snapshot = WorkspaceSnapshot.capture(repository)

    assert snapshot.staged_paths == (destination_path, source_path)
    assert snapshot.unstaged_paths == ()
    assert snapshot.untracked_paths == ()


def test_snapshot_includes_rename_source_and_not_copy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    expected_command = [
        "git",
        "-C",
        str(repository.resolve()),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ]
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                b"R  staged-new\n.txt\0staged-old\r.txt\0"
                b" R unstaged-new\r.txt\0unstaged-old\n.txt\0"
                b"C  copied.txt\0copy-source.txt\0"
                b"?? nested/untracked\r\n.txt\0"
            ),
        )

    monkeypatch.setattr("agent_harness.workspace.subprocess.run", fake_run)

    snapshot = WorkspaceSnapshot.capture(repository)

    assert calls == [
        (
            expected_command,
            {"capture_output": True, "check": False, "env": git_read_environment()},
        )
    ]
    assert snapshot.staged_paths == (
        "staged-new\n.txt",
        "staged-old\r.txt",
        "copied.txt",
    )
    assert snapshot.unstaged_paths == ("unstaged-new\r.txt", "unstaged-old\n.txt")
    assert snapshot.untracked_paths == ("nested/untracked\r\n.txt",)


@pytest.mark.parametrize("selector", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
def test_snapshot_ignores_inherited_git_selectors_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    clean_environment = dict(os.environ)
    repositories = [tmp_path / "requested", tmp_path / "foreign"]
    for repository in repositories:
        git(tmp_path, "init", "--quiet", str(repository))
        for kind in ("staged", "unstaged"):
            (repository / f"{repository.name}-{kind}.txt").write_bytes(b"base")
        git(repository, "add", ".")
        git(
            repository,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        )
        (repository / f"{repository.name}-staged.txt").write_bytes(b"staged")
        git(repository, "add", ".")
        (repository / f"{repository.name}-unstaged.txt").write_bytes(b"unstaged")
        (repository / f"{repository.name}-new.txt").write_bytes(b"untracked")

    def repository_contents(repository: Path) -> tuple[dict[str, bytes], bytes]:
        files = {path.name: path.read_bytes() for path in repository.glob("*.txt")}
        index = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "--stage", "-z"],
            env=clean_environment,
            capture_output=True,
            check=True,
        ).stdout
        return files, index

    before = [repository_contents(repository) for repository in repositories]
    requested, foreign = repositories
    selected_path = {
        "GIT_DIR": foreign / ".git",
        "GIT_WORK_TREE": foreign,
        "GIT_INDEX_FILE": foreign / ".git" / "index",
    }[selector]
    monkeypatch.setenv(selector, str(selected_path))
    parent_environment = dict(os.environ)

    snapshot = WorkspaceSnapshot.capture(requested)

    assert set(snapshot.staged_paths) == {"requested-staged.txt"}
    assert set(snapshot.unstaged_paths) == {"requested-unstaged.txt"}
    assert set(snapshot.untracked_paths) == {"requested-new.txt"}
    assert [repository_contents(repository) for repository in repositories] == before
    assert dict(os.environ) == parent_environment

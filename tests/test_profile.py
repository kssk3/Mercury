from __future__ import annotations

import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from agent_harness import profile as profile_module


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True
    )


def init(repository: Path) -> Path:
    git(repository.parent, "init", "--quiet", str(repository))
    return repository


def test_empty_repository_and_non_git_path(tmp_path: Path) -> None:
    repository = init(tmp_path / "repository")

    result = profile_module.build_repository_profile(repository)

    assert result.repository_root == str(repository.resolve())
    assert result.file_count == 0
    assert result.languages == ()
    assert result.manifests == ()
    assert result.documents == ()
    assert result.top_level_directories == ()
    with pytest.raises(FrozenInstanceError):
        result.file_count = 5  # type: ignore[misc]
    with pytest.raises(ValueError, match="Git working tree"):
        profile_module.build_repository_profile(tmp_path)


def test_profile_uses_regular_worktree_filenames_and_nested_evidence(
    tmp_path: Path,
) -> None:
    repository = init(tmp_path / "repository")
    (repository / "src").mkdir()
    (repository / "docs").mkdir()
    (repository / "nested").mkdir()
    (repository / "other").mkdir()
    (repository / "src" / "main.py").write_text("old", encoding="utf-8")
    (repository / "docs" / "README.md").write_text("readme", encoding="utf-8")
    (repository / "nested" / "package.json").write_text("{}", encoding="utf-8")
    (repository / "deleted.rs").write_text("", encoding="utf-8")
    (repository / ".gitignore").write_text("ignored.go\n", encoding="utf-8")
    git(
        repository,
        "add",
        "src/main.py",
        "docs/README.md",
        "nested/package.json",
        "deleted.rs",
        ".gitignore",
    )
    (repository / "src" / "main.py").write_text("dirty", encoding="utf-8")
    (repository / "deleted.rs").unlink()
    (repository / "ignored.go").write_text("", encoding="utf-8")
    (repository / "new.tsx").write_text("", encoding="utf-8")
    (repository / "Cargo.toml").write_text("", encoding="utf-8")
    (repository / "AGENTS.md").write_text("", encoding="utf-8")
    (repository / "nested" / "go.mod").write_text("", encoding="utf-8")
    (repository / "nested" / "space name.js").write_text("", encoding="utf-8")
    (repository / "nested" / "line\nbreak.mjs").write_text("", encoding="utf-8")
    (repository / "nested" / "UPPER.PY").write_text("", encoding="utf-8")
    (repository / "other" / "GO.MOD").write_text("", encoding="utf-8")
    (repository / "nested" / "engine.rs").write_text("", encoding="utf-8")

    result = profile_module.build_repository_profile(repository / "src" / "main.py")

    assert result.file_count == 13
    assert result.languages == ("JavaScript", "Python", "Rust", "TypeScript")
    assert result.manifests == ("Cargo.toml", "nested/go.mod", "nested/package.json")
    assert result.documents == ("AGENTS.md", "docs/README.md")
    assert result.top_level_directories == ("docs", "nested", "other", "src")
    assert profile_module.build_repository_profile(repository / "nested") == result


def test_profile_does_not_follow_symlinks_or_symlink_ancestors(tmp_path: Path) -> None:
    repository = init(tmp_path / "repository")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("", encoding="utf-8")
    (outside / "inside.ts").write_text("", encoding="utf-8")
    (repository / "entry.py").symlink_to(outside / "secret.py")
    (repository / "folder").mkdir()
    (repository / "folder" / "inside.ts").write_text("", encoding="utf-8")
    git(repository, "add", "entry.py", "folder/inside.ts")
    (repository / "folder" / "inside.ts").unlink()
    (repository / "folder").rmdir()
    (repository / "folder").symlink_to(outside, target_is_directory=True)

    result = profile_module.build_repository_profile(repository)

    assert result.file_count == 0
    assert result.languages == ()
    assert result.top_level_directories == ()


def test_profile_excludes_gitlink_contents(tmp_path: Path) -> None:
    repository = init(tmp_path / "repository")
    submodule = repository / "deps" / "submodule"
    submodule.parent.mkdir()
    init(submodule)
    (submodule / "inside.rs").write_text("", encoding="utf-8")
    git(submodule, "add", "inside.rs")
    git(
        submodule,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--quiet",
        "-m",
        "initial",
    )
    commit = subprocess.run(
        ["git", "-C", str(submodule), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{commit},deps/submodule",
    )

    result = profile_module.build_repository_profile(repository)

    assert result.file_count == 0
    assert result.languages == ()
    assert result.top_level_directories == ()


def test_profile_converts_file_inspection_error_to_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = init(tmp_path / "repository")
    entry = repository / "entry.py"
    entry.write_text("", encoding="utf-8")
    original_lstat = Path.lstat

    def denied_lstat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path == entry:
            raise PermissionError("denied")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    with pytest.raises(ValueError, match="file"):
        profile_module.build_repository_profile(repository)


def test_profile_linked_worktree_and_inherited_git_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = init(tmp_path / "repository")
    (repository / "main.go").write_text("", encoding="utf-8")
    git(repository, "add", "main.go")
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--quiet",
        "-m",
        "initial",
    )
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "--quiet", "-b", "linked", str(linked))
    other = init(tmp_path / "other")
    (linked / "requirements.txt").write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")

    result = profile_module.build_repository_profile(linked)

    assert result.repository_root == str(linked.resolve())
    assert result.file_count == 2
    assert result.languages == ("Go",)
    assert result.manifests == ("requirements.txt",)


def test_profile_does_not_read_contents_or_execute_fsmonitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = init(tmp_path / "repository")
    marker = tmp_path / "ran"
    script = repository / "monitor.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    (repository / "package.json").write_text("not JSON", encoding="utf-8")
    git(repository, "config", "core.fsmonitor", str(script))

    def unexpected_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("profile read file contents")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    monkeypatch.setattr(Path, "read_bytes", unexpected_read)

    result = profile_module.build_repository_profile(repository)

    assert result.manifests == ("package.json",)
    assert result.file_count == 2
    assert not marker.exists()
    assert not (repository / ".git" / "index.lock").exists()


def test_profile_reports_git_inventory_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = init(tmp_path / "repository")
    actual_run = subprocess.run

    def failed_inventory(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if "ls-files" in arguments:
            return subprocess.CompletedProcess(arguments, 128, b"", b"failed")
        return cast(
            subprocess.CompletedProcess[bytes],
            actual_run(arguments, **cast(dict[str, Any], kwargs)),
        )

    monkeypatch.setattr(subprocess, "run", failed_inventory)

    with pytest.raises(ValueError, match="Git"):
        profile_module.build_repository_profile(repository)

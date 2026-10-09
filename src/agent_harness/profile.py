"""Read-only, filename-based evidence about a Git working tree."""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_harness.repository import git_read_environment, resolve_repository_root

_LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
}
_MANIFEST_NAMES = frozenset(
    {"pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod"}
)
_DOCUMENT_NAMES = frozenset({"README.md", "AGENTS.md"})


@dataclass(frozen=True)
class RepositoryProfile:
    repository_root: str
    file_count: int
    languages: tuple[str, ...]
    manifests: tuple[str, ...]
    documents: tuple[str, ...]
    top_level_directories: tuple[str, ...]


def _is_regular_without_symlinks(root: Path, relative: Path) -> bool:
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return False
        if stat.S_ISLNK(mode):
            return False
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            return False
    return stat.S_ISREG(mode)


def build_repository_profile(path: Path | str) -> RepositoryProfile:
    root = resolve_repository_root(path)
    try:
        inventory = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
            ],
            capture_output=True,
            check=False,
            env=git_read_environment(),
        )
    except OSError as error:
        raise ValueError("could not inspect Git file inventory") from error
    if inventory.returncode != 0:
        raise ValueError("could not inspect Git file inventory")

    paths = {
        Path(os.fsdecode(raw_path))
        for raw_path in inventory.stdout.split(b"\0")
        if raw_path
    }
    try:
        included = sorted(
            (
                relative
                for relative in paths
                if _is_regular_without_symlinks(root, relative)
            ),
            key=lambda relative: relative.as_posix(),
        )
    except OSError as error:
        raise ValueError("could not inspect working-tree files") from error
    languages = {
        _LANGUAGE_SUFFIXES[path.suffix]
        for path in included
        if path.suffix in _LANGUAGE_SUFFIXES
    }
    manifests = {path.as_posix() for path in included if path.name in _MANIFEST_NAMES}
    documents = {path.as_posix() for path in included if path.name in _DOCUMENT_NAMES}
    top_level = {path.parts[0] for path in included if len(path.parts) > 1}
    return RepositoryProfile(
        repository_root=str(root),
        file_count=len(included),
        languages=tuple(sorted(languages)),
        manifests=tuple(sorted(manifests)),
        documents=tuple(sorted(documents)),
        top_level_directories=tuple(sorted(top_level)),
    )

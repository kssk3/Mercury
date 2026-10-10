from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryStateLocation:
    repository_root: Path
    repository_id: str
    state_directory: Path


def resolve_repository_state_location(
    repository_path: Path | str,
    state_root: Path | str | None = None,
) -> RepositoryStateLocation:
    repository_root = resolve_repository_root(repository_path)
    resolved_state_root = _resolve_state_root(state_root)
    repository_id = hashlib.sha256(os.fsencode(str(repository_root))).hexdigest()
    repositories_directory = resolved_state_root / "repositories"
    state_directory = repositories_directory / repository_id
    if repositories_directory.is_symlink() or state_directory.is_symlink():
        raise ValueError(
            "state directory must be outside the repository without symbolic links"
        )
    state_directory = state_directory.resolve()

    if resolved_state_root.is_relative_to(
        repository_root
    ) or state_directory.is_relative_to(repository_root):
        raise ValueError("state root must be outside the repository")

    return RepositoryStateLocation(
        repository_root=repository_root,
        repository_id=repository_id,
        state_directory=state_directory,
    )


def git_read_environment() -> dict[str, str]:
    """Remove inherited Git selectors so a supplied path chooses the repository."""
    return {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }


def resolve_repository_root(repository_path: Path | str) -> Path:
    candidate = Path(repository_path).expanduser()
    if candidate.is_symlink():
        candidate = candidate.parent
    candidate = candidate.resolve()
    if not candidate.exists():
        raise ValueError("repository path must exist inside a Git working tree")
    if candidate.is_file():
        candidate = candidate.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            env=git_read_environment(),
            text=True,
        )
    except OSError as error:
        raise ValueError("could not inspect Git working tree") from error
    if result.returncode != 0:
        raise ValueError("repository path must be inside a Git working tree")
    return Path(result.stdout.removesuffix("\n")).resolve()


def _resolve_state_root(state_root: Path | str | None) -> Path:
    if state_root is not None:
        return Path(state_root).expanduser().resolve()
    return (Path.home() / "Library" / "Application Support" / "agent-harness").resolve()

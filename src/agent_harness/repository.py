from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Resolve from system paths before any native turn can alter repository-local PATH.
_SYSTEM_GIT = shutil.which("git", path=os.defpath)
_TRUSTED_GIT = str(Path(_SYSTEM_GIT).resolve()) if _SYSTEM_GIT else None


def git_executable() -> str:
    """Internal queries never select an executable from the mutable target PATH."""
    if _TRUSTED_GIT is None:
        raise ValueError("trusted system Git executable is unavailable")
    return _TRUSTED_GIT


def _git_query(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = git_read_environment()
    environment["LC_ALL"] = "C"
    try:
        return subprocess.run(
            [git_executable(), "-C", str(directory), "rev-parse", *arguments],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("could not inspect runtime state location") from error


def _is_within_directory(path: Path, directory: Path) -> bool:
    """Include existing ancestor aliases without folding distinct path names."""
    if path.is_relative_to(directory):
        return True
    try:
        protected = directory.stat()
    except FileNotFoundError:
        return False
    for ancestor in (path, *path.parents):
        try:
            observed = ancestor.stat()
        except FileNotFoundError:
            continue
        if os.path.samestat(observed, protected):
            return True
    return False


def validate_runtime_state_directory(
    directory: Path | str,
    repository_root: Path | None = None,
    *,
    admitted_metadata: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    """Reject evidence storage in working trees or their actual Git metadata."""
    path = Path(directory).expanduser()
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError("runtime state ancestors must not be symbolic links")
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError("runtime state ancestors must be directories")
    canonical = path.resolve()
    if repository_root is not None:
        root = repository_root.resolve()
        if _is_within_directory(canonical, root):
            raise ValueError("runtime state must be outside the repository")
        metadata_roots = list(admitted_metadata)
        live_roots: list[Path] = []
        for selector in ("--absolute-git-dir", "--git-common-dir"):
            result = _git_query(root, selector)
            if result.returncode:
                if not admitted_metadata:
                    raise ValueError("could not inspect repository Git metadata")
                break
            metadata = Path(result.stdout.removesuffix("\n"))
            if not metadata.is_absolute():
                metadata = root / metadata
            metadata = metadata.resolve()
            # A live unsafe location always rejects, including partial queries.
            if _is_within_directory(canonical, metadata):
                raise ValueError("runtime state must be outside Git metadata")
            live_roots.append(metadata)
        else:
            metadata_roots.extend(live_roots)
        if any(
            _is_within_directory(canonical, metadata) for metadata in metadata_roots
        ):
            raise ValueError("runtime state must be outside Git metadata")
        return tuple(dict.fromkeys(metadata_roots))
    existing = canonical
    while not existing.exists():
        existing = existing.parent
    result = _git_query(existing, "--is-inside-work-tree", "--is-inside-git-dir")
    if result.returncode:
        if result.returncode != 128 or not result.stderr.startswith(
            "fatal: not a git repository"
        ):
            raise ValueError("could not inspect runtime state location")
        return ()
    if "true" in result.stdout.splitlines():
        raise ValueError(
            "runtime state must be outside the repository and Git metadata"
        )
    return ()


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

    validate_runtime_state_directory(resolved_state_root, repository_root)
    validate_runtime_state_directory(state_directory, repository_root)

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
            [git_executable(), "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            env=git_read_environment(),
            text=True,
            timeout=10,
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


def read_persistence_bytes(path: Path) -> bytes | None:
    """Read one opened evidence leaf, bounded by its initial size.

    Existing history has no fixed size ceiling. Read finite chunks up to the
    initial descriptor size, then one byte to detect growth. This is a
    point-in-time check, not an atomic snapshot or same-size ABA guarantee.
    """
    validate_runtime_state_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("persistence must be a regular file with one link")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                raise ValueError("persistence changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        extra = os.read(descriptor, 1)
        after = os.fstat(descriptor)
        if (
            extra
            or after.st_size != before.st_size
            or after.st_nlink != 1
            or not stat.S_ISREG(after.st_mode)
        ):
            raise ValueError("persistence changed during read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)

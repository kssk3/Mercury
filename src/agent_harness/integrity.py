from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG


class ProtectedInputState(StrEnum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class ProtectedInputFingerprint:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 hexadecimal digest")


@dataclass(frozen=True)
class ProtectedDirectoryFingerprint:
    """A protected directory's relative regular-file names and content digests."""

    path: str
    files: tuple[ProtectedInputFingerprint, ...]

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        files = _normalize_fingerprints(self.files)
        object.__setattr__(
            self,
            "files",
            tuple(sorted(files, key=lambda fingerprint: fingerprint.path)),
        )


@dataclass(frozen=True)
class ProtectedInputCheck:
    path: str
    state: ProtectedInputState


def capture_protected_inputs(
    repository_root: Path | str,
    protected_paths: Iterable[str],
) -> tuple[ProtectedInputFingerprint, ...]:
    root = Path(repository_root).expanduser().resolve()
    paths = _normalize_paths(protected_paths)
    return tuple(
        ProtectedInputFingerprint(
            path=path,
            sha256=sha256(_read_capturable_bytes(root, path)).hexdigest(),
        )
        for path in paths
    )


def verify_protected_inputs(
    repository_root: Path | str,
    fingerprints: Iterable[ProtectedInputFingerprint],
) -> tuple[ProtectedInputCheck, ...]:
    root = Path(repository_root).expanduser().resolve()
    captured = _normalize_fingerprints(fingerprints)
    return tuple(
        ProtectedInputCheck(fingerprint.path, _state_for(root, fingerprint))
        for fingerprint in captured
    )


def _normalize_paths(paths: Iterable[str]) -> tuple[str, ...]:
    if isinstance(paths, str):
        raise ValueError("protected paths must be a collection of relative paths")
    normalized_paths = tuple(_validate_relative_path(path) for path in paths)
    if len(set(normalized_paths)) != len(normalized_paths):
        raise ValueError("protected paths must not contain duplicates")
    return normalized_paths


def _normalize_fingerprints(
    fingerprints: Iterable[ProtectedInputFingerprint],
) -> tuple[ProtectedInputFingerprint, ...]:
    if isinstance(fingerprints, ProtectedInputFingerprint):
        raise ValueError("fingerprints must be a collection")
    captured = tuple(fingerprints)
    if not all(
        isinstance(fingerprint, ProtectedInputFingerprint) for fingerprint in captured
    ):
        raise ValueError("fingerprints must be ProtectedInputFingerprint values")
    paths = tuple(fingerprint.path for fingerprint in captured)
    if len(set(paths)) != len(paths):
        raise ValueError("fingerprints must not contain duplicate paths")
    return captured


def _validate_relative_path(path: object) -> str:
    if not isinstance(path, str) or not path or path != path.strip():
        raise ValueError("protected path must be nonblank")
    if "\\" in path or "\x00" in path:
        raise ValueError("protected path must use normalized relative path syntax")

    parsed_path = PurePosixPath(path)
    if (
        parsed_path.is_absolute()
        or ".." in parsed_path.parts
        or path in {".", ".."}
        or parsed_path.as_posix() != path
    ):
        raise ValueError("protected path must be a safe normalized relative path")
    return path


def _read_capturable_bytes(root: Path, path: str) -> bytes:
    candidate = root / path
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("protected input must be an existing regular non-symlink file")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("protected input must be resolvable") from error
    if not resolved.is_relative_to(root):
        raise ValueError("protected input must resolve inside the repository root")
    try:
        return candidate.read_bytes()
    except OSError as error:
        raise ValueError("protected input must be readable") from error


def _state_for(
    root: Path, fingerprint: ProtectedInputFingerprint
) -> ProtectedInputState:
    candidate = root / fingerprint.path
    try:
        if candidate.is_symlink():
            return ProtectedInputState.UNVERIFIABLE
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not candidate.is_file():
            return ProtectedInputState.UNVERIFIABLE
    except FileNotFoundError:
        return ProtectedInputState.MISSING
    except (OSError, RuntimeError):
        return ProtectedInputState.UNVERIFIABLE
    try:
        actual_digest = sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return ProtectedInputState.UNVERIFIABLE
    if actual_digest == fingerprint.sha256:
        return ProtectedInputState.UNCHANGED
    return ProtectedInputState.MODIFIED


def capture_protected_directories(
    repository_root: Path | str,
    protected_paths: Iterable[str],
) -> tuple[ProtectedDirectoryFingerprint, ...]:
    """Capture protected directory contents without creating target-tree files."""
    paths = _normalize_paths(protected_paths)
    try:
        root = Path(repository_root).expanduser().resolve()
        return tuple(
            ProtectedDirectoryFingerprint(
                path, _read_directory_files(_locate_protected_directory(root, path))
            )
            for path in paths
        )
    except (OSError, RuntimeError) as error:
        raise ValueError("protected directory must be safely readable") from error


def verify_protected_directories(
    repository_root: Path | str,
    fingerprints: Iterable[ProtectedDirectoryFingerprint],
) -> tuple[ProtectedInputCheck, ...]:
    """Compare directory contents through the existing evidence check boundary."""
    captured = _normalize_directory_fingerprints(fingerprints)
    try:
        root = Path(repository_root).expanduser().resolve()
    except (OSError, RuntimeError):
        return tuple(
            ProtectedInputCheck(fingerprint.path, ProtectedInputState.UNVERIFIABLE)
            for fingerprint in captured
        )
    return tuple(
        ProtectedInputCheck(fingerprint.path, _directory_state_for(root, fingerprint))
        for fingerprint in captured
    )


def _normalize_directory_fingerprints(
    fingerprints: Iterable[ProtectedDirectoryFingerprint],
) -> tuple[ProtectedDirectoryFingerprint, ...]:
    if isinstance(fingerprints, ProtectedDirectoryFingerprint):
        raise ValueError("directory fingerprints must be a collection")
    captured = tuple(fingerprints)
    if not all(isinstance(item, ProtectedDirectoryFingerprint) for item in captured):
        raise ValueError("fingerprints must be ProtectedDirectoryFingerprint values")
    paths = tuple(fingerprint.path for fingerprint in captured)
    if len(set(paths)) != len(paths):
        raise ValueError("directory fingerprints must not contain duplicate paths")
    return captured


def _directory_state_for(
    root: Path, fingerprint: ProtectedDirectoryFingerprint
) -> ProtectedInputState:
    try:
        directory = _locate_protected_directory(root, fingerprint.path)
    except FileNotFoundError:
        return ProtectedInputState.MISSING
    except (OSError, ValueError, RuntimeError):
        return ProtectedInputState.UNVERIFIABLE
    try:
        actual = _read_directory_files(directory)
    except (OSError, ValueError, RuntimeError):
        return ProtectedInputState.UNVERIFIABLE
    return (
        ProtectedInputState.UNCHANGED
        if actual == fingerprint.files
        else ProtectedInputState.MODIFIED
    )


def _locate_protected_directory(root: Path, path: str) -> Path:
    """Check every component below the canonical root without following links."""
    candidate = root
    for part in ("", *PurePosixPath(path).parts):
        candidate = candidate / part
        if not S_ISDIR(candidate.lstat().st_mode):
            raise ValueError("protected directory path must contain only directories")
    return candidate


def _read_directory_files(directory: Path) -> tuple[ProtectedInputFingerprint, ...]:
    files = []
    pending = [directory]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            relative_path = _validate_relative_path(
                entry.relative_to(directory).as_posix()
            )
            mode = entry.lstat().st_mode
            if S_ISDIR(mode):
                pending.append(entry)
            elif S_ISREG(mode):
                files.append(
                    ProtectedInputFingerprint(
                        relative_path, sha256(entry.read_bytes()).hexdigest()
                    )
                )
            else:
                raise ValueError(
                    "protected directory entries must be regular files or directories"
                )
    return tuple(sorted(files, key=lambda fingerprint: fingerprint.path))

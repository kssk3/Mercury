"""Explicit exact file recovery; caller serializes writers using the B4 lock.

Snapshots are point-in-time evidence, not hostile-writer isolation. Trusted
references must be retained separately from private artifacts. No journal hook.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from agent_harness.contract import TaskContract
from agent_harness.repository import git_read_environment, resolve_repository_root
from agent_harness.scope import _validate_observation
from agent_harness.turn import FileFingerprint, GitStatusEntry, RepositoryObservation

MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class FileImage:
    path: str
    data: bytes | None
    mode: int | None


@dataclass(frozen=True)
class PatchSnapshot:
    observation: RepositoryObservation
    contract_sha256: str
    attempt_id: str
    images: tuple[FileImage, ...]


@dataclass(frozen=True)
class ArtifactReference:
    path: Path
    sha256: str
    repository_id: str
    contract_sha256: str
    attempt_id: str


@dataclass(frozen=True)
class RecoveryResult:
    status: Literal["restored", "already_restored", "partial_unknown"]
    restored_paths: tuple[str, ...]


def _digest(contract: TaskContract) -> str:
    return hashlib.sha256(contract.to_json().encode()).hexdigest()


def _scope(path: str, contract: TaskContract) -> None:
    parsed = PurePosixPath(path)
    if (
        not path
        or parsed.is_absolute()
        or str(parsed) != path
        or "\0" in path
        or any(p in {".", ".."} or p.casefold() == ".git" for p in parsed.parts)
    ):
        raise ValueError("unsafe recovery path")

    def matches(parent: str) -> bool:
        return path.casefold() == parent.casefold() or path.casefold().startswith(
            parent.casefold() + "/"
        )

    if not any(
        path == p or path.startswith(p + "/") for p in contract.allowed_paths
    ) or any(matches(p) for p in contract.protected_paths):
        raise ValueError("recovery path outside admitted scope")


def _safe_path(root: Path, relative: str, *, create: bool = False) -> Path:
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if create:
                current.mkdir(mode=0o700)
                mode = current.lstat().st_mode
            else:
                continue
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise ValueError("unsafe recovery ancestor")
    return current / PurePosixPath(relative).name


def _image(root: Path, relative: str) -> FileImage:
    path = _safe_path(root, relative)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return FileImage(relative, None, None)
    except OSError:
        raise ValueError("unsafe recovery target") from None
    try:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or mode & 0o7000:
            raise ValueError("unsupported recovery target")
        data = bytearray()
        while chunk := os.read(descriptor, min(65536, MAX_BYTES + 1 - len(data))):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("recovery image exceeds budget")
        return FileImage(relative, bytes(data), mode)
    finally:
        os.close(descriptor)


def capture_patch(
    repository: Path | str,
    contract: TaskContract,
    attempt_id: str,
    paths: tuple[str, ...],
) -> PatchSnapshot:
    """Capture exact explicitly named images, cross-checked with F2 observation."""
    if (
        not attempt_id
        or len(attempt_id) > 256
        or not paths
        or len(paths) > 1024
        or len(set(p.casefold() for p in paths)) != len(paths)
    ):
        raise ValueError("invalid recovery identity or paths")
    root = resolve_repository_root(repository)
    for path in paths:
        _scope(path, contract)
    before = RepositoryObservation.capture(root)
    images = tuple(_image(root, path) for path in paths)
    if sum(len(image.data or b"") for image in images) > MAX_BYTES:
        raise ValueError("recovery images exceed budget")
    after = RepositoryObservation.capture(root)
    if before != after:
        raise ValueError("workspace changed during recovery capture")
    observed = {entry.path: entry for entry in after.files}
    for image in images:
        entry = observed.get(image.path)
        if image.data is None:
            if entry is not None and entry.kind != "missing":
                raise ValueError("recovery image observation disagreement")
        elif (
            entry is None
            or entry.kind != "regular"
            or entry.sha256 != hashlib.sha256(image.data).hexdigest()
            or entry.size_bytes != len(image.data)
            or entry.executable != bool((image.mode or 0) & 0o111)
            or entry.mode != image.mode
        ):
            raise ValueError("recovery image observation disagreement")
    return PatchSnapshot(after, _digest(contract), attempt_id, images)


def _external(path: Path, root: Path) -> None:
    if not path.is_absolute() or path != path.resolve() or path.is_relative_to(root):
        raise ValueError("artifact must be canonical and external")
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError("unsafe artifact path")
    # Linked worktree Git metadata can be external to the working tree.
    for selector in ("--absolute-git-dir", "--git-common-dir"):
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", selector],
            check=True,
            capture_output=True,
            env=git_read_environment(),
            timeout=10,
        )
        directory = Path(os.fsdecode(result.stdout).strip())
        if not directory.is_absolute():
            directory = root / directory
        if path.is_relative_to(directory.resolve()):
            raise ValueError("artifact must be outside Git metadata")


def _stable_stat(info: os.stat_result) -> tuple[int, ...]:
    # Reading legitimately changes atime on some filesystems.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _hex(value: object, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def _canonical(relative: str) -> None:
    parsed = PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or str(parsed) != relative
        or "\0" in relative
        or any(p in {".", ".."} or p.casefold() == ".git" for p in parsed.parts)
    ):
        raise ValueError("unsafe recovery evidence path")


def _tracked_paths(root: Path, expected_index: str) -> set[str]:
    environment = git_read_environment()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                "ls-files",
                "--stage",
                "-v",
                "-z",
            ],
            capture_output=True,
            check=True,
            env=environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("could not inspect recovery index") from None
    payload = result.stdout
    if payload and not payload.endswith(b"\0"):
        raise ValueError("invalid recovery index")
    records = payload[:-1].split(b"\0") if payload else []
    digest = hashlib.sha256(
        b"\0".join(sorted(records)) + (b"\0" if records else b"")
    ).hexdigest()
    if digest != expected_index:
        raise ValueError("recovery index changed")
    paths: set[str] = set()
    for record in records:
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator or metadata[2:].split(b" ")[0] not in {
            b"100644",
            b"100755",
            b"120000",
        }:
            raise ValueError("unsupported recovery index entry")
        relative = os.fsdecode(raw_path)
        _canonical(relative)
        paths.add(relative)
    return paths


def _validate_presence(observation: RepositoryObservation, root: Path) -> None:
    tracked = _tracked_paths(root, observation.index_sha256)
    files = {entry.path: entry for entry in observation.files}
    if not tracked <= files.keys():
        raise ValueError("incomplete tracked recovery inventory")
    if any(
        entry.kind == "missing" and entry.path not in tracked
        for entry in observation.files
    ):
        raise ValueError("untracked missing recovery inventory")
    for entry in observation.status:
        fingerprint = files.get(entry.path)
        present = fingerprint is not None and fingerprint.kind != "missing"
        if (entry.status == "??" and not present) or (
            entry.status[1] == "D" and present
        ):
            raise ValueError("contradictory recovery file presence")


def _validate_snapshot(snapshot: PatchSnapshot) -> None:
    """Validate untrusted caller-constructed values as well as decoded evidence."""
    obs = snapshot.observation
    _validate_observation(obs)
    root = Path(obs.repository_root)
    if (
        not root.is_absolute()
        or root != root.resolve()
        or not _hex(snapshot.contract_sha256)
        or not isinstance(snapshot.attempt_id, str)
        or not snapshot.attempt_id
        or len(snapshot.attempt_id) > 256
    ):
        raise ValueError("invalid recovery snapshot identity")
    _validate_presence(obs, root)
    if not snapshot.images or len(snapshot.images) > 1024:
        raise ValueError("invalid recovery images")
    fingerprints = {entry.path: entry for entry in obs.files}
    seen: set[str] = set()
    total = 0
    for image in snapshot.images:
        _canonical(image.path)
        if image.path.casefold() in seen:
            raise ValueError("duplicate recovery image")
        seen.add(image.path.casefold())
        fingerprint = fingerprints.get(image.path)
        if image.data is None:
            valid = image.mode is None and (
                fingerprint is None or fingerprint.kind == "missing"
            )
        else:
            valid = (
                isinstance(image.data, bytes)
                and type(image.mode) is int
                and 0 <= image.mode <= 0o777
                and fingerprint is not None
                and fingerprint.kind == "regular"
                and fingerprint.sha256 == hashlib.sha256(image.data).hexdigest()
                and fingerprint.size_bytes == len(image.data)
                and fingerprint.executable == bool(image.mode & 0o111)
                and fingerprint.mode == image.mode
            )
            total += len(image.data)
        if not valid:
            raise ValueError("recovery image observation disagreement")
    if total > MAX_BYTES:
        raise ValueError("recovery images exceed budget")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate artifact JSON key")
        result[key] = value
    return result


def _bad_constant(value: str) -> object:
    raise ValueError("invalid artifact JSON constant")


def _encoded(snapshot: PatchSnapshot) -> dict[str, object]:
    return {
        "observation": asdict(snapshot.observation),
        "contract_sha256": snapshot.contract_sha256,
        "attempt_id": snapshot.attempt_id,
        "images": [
            {
                "path": i.path,
                "data": None
                if i.data is None
                else base64.b64encode(i.data).decode("ascii"),
                "mode": i.mode,
            }
            for i in snapshot.images
        ],
    }


def _status_codes(observation: RepositoryObservation) -> dict[str, set[str]]:
    codes: dict[str, set[str]] = {}
    for entry in observation.status:
        codes.setdefault(entry.path, set()).add(entry.status)
    return codes


def _index_status(observation: RepositoryObservation) -> set[tuple[str, str]]:
    return {
        (entry.path, entry.status[0])
        for entry in observation.status
        if entry.status != "??" and entry.status[0] != " "
    }


def _complete_delta(before: PatchSnapshot, after: PatchSnapshot) -> None:
    if _index_status(before.observation) != _index_status(after.observation):
        raise ValueError("contradictory fixed-index recovery status")
    targets = {image.path for image in before.images}
    old = {entry.path: entry for entry in before.observation.files}
    new = {entry.path: entry for entry in after.observation.files}
    changed = {
        path for path in old.keys() | new.keys() if old.get(path) != new.get(path)
    }
    old_status = _status_codes(before.observation)
    new_status = _status_codes(after.observation)
    changed.update(
        path
        for path in old_status.keys() | new_status.keys()
        if old_status.get(path) != new_status.get(path)
    )
    if not changed <= targets:
        raise ValueError("incomplete recovery file delta")


def seal_patch(
    before: PatchSnapshot, after: PatchSnapshot, artifact: Path
) -> ArtifactReference:
    """Seal complete evidence privately; return the caller's trusted reference."""
    if (
        before.contract_sha256 != after.contract_sha256
        or before.attempt_id != after.attempt_id
        or before.observation.repository_root != after.observation.repository_root
        or before.observation.repository_id != after.observation.repository_id
        or tuple(i.path for i in before.images) != tuple(i.path for i in after.images)
        or before.observation.head != after.observation.head
        or before.observation.head_ref != after.observation.head_ref
        or before.observation.index_sha256 != after.observation.index_sha256
    ):
        raise ValueError("incompatible recovery snapshots")
    _validate_snapshot(before)
    _validate_snapshot(after)
    _complete_delta(before, after)
    root = Path(before.observation.repository_root)
    _external(artifact, root)
    payload = json.dumps(
        {"version": 1, "before": _encoded(before), "after": _encoded(after)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("recovery artifact exceeds budget")
    descriptor = os.open(
        artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        artifact.unlink(missing_ok=True)
        raise
    return ArtifactReference(
        artifact,
        hashlib.sha256(payload).hexdigest(),
        before.observation.repository_id,
        before.contract_sha256,
        before.attempt_id,
    )


def _load_snapshot(
    raw: object, root: Path, contract: TaskContract, attempt: str
) -> PatchSnapshot:
    # Compare to a freshly captured snapshot to validate observation shape and images.
    if (
        not isinstance(raw, dict)
        or set(raw) != {"observation", "contract_sha256", "attempt_id", "images"}
        or raw["contract_sha256"] != _digest(contract)
        or raw["attempt_id"] != attempt
        or not isinstance(raw["images"], list)
    ):
        raise ValueError("invalid recovery snapshot")
    images = []
    for entry in raw["images"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "data", "mode"}
            or not isinstance(entry["path"], str)
        ):
            raise ValueError("invalid recovery image")
        _scope(entry["path"], contract)
        data, mode = entry["data"], entry["mode"]
        if data is None and mode is None:
            decoded = None
        elif isinstance(data, str) and type(mode) is int and 0 <= mode <= 0o777:
            decoded = base64.b64decode(data, validate=True)
        else:
            raise ValueError("invalid recovery image")
        images.append(FileImage(entry["path"], decoded, mode))
    if (
        not images
        or len(images) > 1024
        or len(set(i.path.casefold() for i in images)) != len(images)
    ):
        raise ValueError("invalid recovery images")
    observation = raw["observation"]
    if (
        not isinstance(observation, dict)
        or set(observation)
        != {
            "repository_root",
            "repository_id",
            "files",
            "head",
            "head_ref",
            "index_sha256",
            "status",
        }
        or observation.get("repository_root") != str(root)
        or not isinstance(observation["files"], list)
        or not isinstance(observation["status"], list)
    ):
        raise ValueError("foreign recovery observation")
    # Reuse existing immutable value types; malformed evidence fails closed.
    observed = RepositoryObservation(
        repository_root=observation["repository_root"],
        repository_id=observation["repository_id"],
        files=tuple(FileFingerprint(**i) for i in observation["files"]),
        head=observation["head"],
        head_ref=observation["head_ref"],
        index_sha256=observation["index_sha256"],
        status=tuple(GitStatusEntry(**i) for i in observation["status"]),
    )
    snapshot = PatchSnapshot(observed, _digest(contract), attempt, tuple(images))
    _validate_snapshot(snapshot)
    return snapshot


def recover_patch(
    repository: Path | str,
    contract: TaskContract,
    attempt_id: str,
    reference: ArtifactReference,
) -> RecoveryResult:
    """Explicit recovery after all-target preflight; retain evidence on failure.

    Necessary missing parent directories may be created. Directory metadata is
    not restored; directories are never recursively removed. Apply I/O failure
    is partial_unknown even when no file replacement has completed.
    """
    root = resolve_repository_root(repository)
    _external(reference.path, root)
    live = RepositoryObservation.capture(root)
    if (
        reference.repository_id != live.repository_id
        or reference.contract_sha256 != _digest(contract)
        or reference.attempt_id != attempt_id
    ):
        raise ValueError("recovery identity mismatch")
    try:
        info = reference.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > MAX_BYTES
        ):
            raise ValueError("unsafe recovery artifact")
        descriptor = os.open(
            reference.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stable_stat(opened) != _stable_stat(info):
                raise ValueError("artifact changed during read")
            payload = stream.read(MAX_BYTES + 1)
            if _stable_stat(os.fstat(stream.fileno())) != _stable_stat(opened):
                raise ValueError("artifact changed during read")
        if (
            len(payload) > MAX_BYTES
            or hashlib.sha256(payload).hexdigest() != reference.sha256
        ):
            raise ValueError("recovery artifact integrity mismatch")
        raw = json.loads(
            payload, object_pairs_hook=_unique_object, parse_constant=_bad_constant
        )
        if (
            not isinstance(raw, dict)
            or set(raw) != {"version", "before", "after"}
            or type(raw["version"]) is not int
            or raw["version"] != 1
        ):
            raise ValueError("invalid recovery artifact")
        before = _load_snapshot(raw["before"], root, contract, attempt_id)
        after = _load_snapshot(raw["after"], root, contract, attempt_id)
        if (
            before.observation.repository_id != reference.repository_id
            or after.observation.repository_id != reference.repository_id
            or tuple(i.path for i in before.images)
            != tuple(i.path for i in after.images)
            or before.observation.head != after.observation.head
            or before.observation.head_ref != after.observation.head_ref
            or before.observation.index_sha256 != after.observation.index_sha256
        ):
            raise ValueError("invalid recovery correlation")
        _complete_delta(before, after)
        current = tuple(_image(root, i.path) for i in after.images)
        if live == before.observation and current == before.images:
            return RecoveryResult("already_restored", ())
        if live != after.observation or current != after.images:
            raise ValueError("recovery workspace conflict")
    except (
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        raise ValueError("invalid or unreadable recovery artifact") from None
    restored: list[str] = []
    try:
        for old, new in zip(before.images, after.images, strict=True):
            if old == new:
                continue
            path = _safe_path(root, old.path)
            if _image(root, old.path) != new:
                return RecoveryResult("partial_unknown", tuple(restored))
            if old.data is None:
                path.unlink()
            else:
                path = _safe_path(root, old.path, create=True)
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".recovery-", dir=path.parent
                )
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(old.data)
                        os.fchmod(stream.fileno(), old.mode or 0)
                    _safe_path(root, old.path)
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            restored.append(old.path)
        if (
            tuple(_image(root, i.path) for i in before.images) != before.images
            or RepositoryObservation.capture(root) != before.observation
        ):
            return RecoveryResult("partial_unknown", tuple(restored))
    except BaseException:
        # Interruption after entering apply cannot prove zero effects.
        return RecoveryResult("partial_unknown", tuple(restored))
    return RecoveryResult("restored", tuple(restored))

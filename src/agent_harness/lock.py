from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class LockUnavailableError(RuntimeError):
    """Raised when another owner holds a live repository lease."""


class LockOwnershipError(RuntimeError):
    """Raised when an operation is attempted by a non-owner."""


@dataclass(frozen=True)
class RepositoryLease:
    owner_token: str
    heartbeat_at: float


class RepositoryLock:
    def __init__(
        self,
        state_directory: Path | str,
        *,
        timeout_seconds: float,
        now: Callable[[], float] = time.time,
        owner_token: str | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")

        if owner_token is not None and (
            not isinstance(owner_token, str) or not owner_token
        ):
            raise ValueError("owner_token must be a nonempty string")

        self._state_directory = Path(state_directory)
        self._timeout_seconds = timeout_seconds
        self._now = now
        self._owner_token = uuid.uuid4().hex if owner_token is None else owner_token

    @property
    def path(self) -> Path:
        return self._state_directory / "lock"

    @property
    def _metadata_path(self) -> Path:
        return self.path / "lease.json"

    @property
    def _transition_path(self) -> Path:
        return self._state_directory / ".lock-transition"

    def acquire(self) -> RepositoryLease:
        self._state_directory.mkdir(parents=True, exist_ok=True)

        with self._ownership_transition():
            while True:
                try:
                    self.path.mkdir()
                except FileExistsError:
                    lease = self._read_lease()
                    if not self._is_expired(lease):
                        raise LockUnavailableError(
                            "repository lock is active"
                        ) from None
                    self._reclaim_expired_lock()
                else:
                    lease = RepositoryLease(self._owner_token, self._now())
                    try:
                        self._write_lease(lease)
                    except BaseException:
                        self.path.rmdir()
                        raise
                    return lease

    def heartbeat(self) -> RepositoryLease:
        with self._ownership_transition():
            self._require_owner()
            lease = RepositoryLease(self._owner_token, self._now())
            self._write_lease(lease)
            return lease

    def release(self) -> None:
        with self._ownership_transition():
            self._require_owner()
            shutil.rmtree(self.path)

    def _is_expired(self, lease: RepositoryLease) -> bool:
        return self._now() - lease.heartbeat_at > self._timeout_seconds

    def _read_lease(self) -> RepositoryLease:
        try:
            payload = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError) as error:
            raise LockUnavailableError("repository lock metadata is invalid") from error

        if not isinstance(payload, dict):
            raise LockUnavailableError("repository lock metadata is invalid")
        owner_token = payload.get("owner_token")
        heartbeat_at = payload.get("heartbeat_at")
        if (
            not isinstance(owner_token, str)
            or not owner_token
            or not isinstance(heartbeat_at, (int, float))
            or isinstance(heartbeat_at, bool)
        ):
            raise LockUnavailableError("repository lock metadata is invalid")
        try:
            heartbeat = float(heartbeat_at)
        except OverflowError as error:
            raise LockUnavailableError("repository lock metadata is invalid") from error
        if not math.isfinite(heartbeat):
            raise LockUnavailableError("repository lock metadata is invalid")
        return RepositoryLease(owner_token, heartbeat)

    def _write_lease(self, lease: RepositoryLease) -> None:
        temporary_path: Path | None = None
        serialized = json.dumps(
            {"heartbeat_at": lease.heartbeat_at, "owner_token": lease.owner_token},
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self.path,
                encoding="utf-8",
                prefix=".lease-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(serialized)
                temporary_file.flush()
            os.replace(temporary_path, self._metadata_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _reclaim_expired_lock(self) -> None:
        reclaimed_path = self._state_directory / f".reclaimed-{uuid.uuid4().hex}"
        try:
            os.replace(self.path, reclaimed_path)
        except FileNotFoundError:
            return
        shutil.rmtree(reclaimed_path)

    @contextmanager
    def _ownership_transition(self) -> Iterator[None]:
        try:
            transition_file = self._transition_path.open("a+b")
        except FileNotFoundError as error:
            raise LockUnavailableError("repository lock metadata is invalid") from error
        with transition_file:
            try:
                fcntl.flock(transition_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LockUnavailableError(
                    "repository lock transition is active"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(transition_file.fileno(), fcntl.LOCK_UN)

    def _require_owner(self) -> None:
        if self._read_lease().owner_token != self._owner_token:
            raise LockOwnershipError("repository lock is owned by another owner")

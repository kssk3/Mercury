from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from typing import Literal

import pytest

from agent_harness.lock import (
    LockOwnershipError,
    LockUnavailableError,
    RepositoryLease,
    RepositoryLock,
)

PROCESS_TIMEOUT_SECONDS = 5.0
RaceAction = Literal["acquire", "release", "heartbeat"]


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class PauseAfterLeaseReadLock(RepositoryLock):
    def __init__(
        self,
        state_directory: Path,
        *,
        owner_token: str,
        lease_read: ProcessEvent,
        contender_finished: ProcessEvent,
    ) -> None:
        super().__init__(
            state_directory,
            timeout_seconds=10,
            now=lambda: 111.0,
            owner_token=owner_token,
        )
        self._lease_read = lease_read
        self._contender_finished = contender_finished

    def _read_lease(self) -> RepositoryLease:
        lease = super()._read_lease()
        self._lease_read.set()
        if not self._contender_finished.wait(PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("contender did not finish")
        return lease


class PauseAfterOwnerCheckLock(RepositoryLock):
    def __init__(
        self,
        state_directory: Path,
        *,
        owner_token: str,
        owner_checked: ProcessEvent,
        contender_finished: ProcessEvent,
    ) -> None:
        super().__init__(
            state_directory,
            timeout_seconds=10,
            now=lambda: 111.0,
            owner_token=owner_token,
        )
        self._owner_checked = owner_checked
        self._contender_finished = contender_finished

    def _require_owner(self) -> None:
        super()._require_owner()
        self._owner_checked.set()
        if not self._contender_finished.wait(PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("contender did not finish")


def record_lock_action(
    action: Callable[[], object],
    outcome_path: Path,
    finished: ProcessEvent | None = None,
) -> None:
    outcome = "unexpected-error"
    try:
        action()
    except LockUnavailableError:
        outcome = "unavailable"
    except LockOwnershipError:
        outcome = "ownership-error"
    else:
        outcome = "success"
    finally:
        outcome_path.write_text(outcome, encoding="utf-8")
        if finished is not None:
            finished.set()


def run_paused_action(
    state_directory: Path,
    owner_token: str,
    transition_reached: ProcessEvent,
    contender_finished: ProcessEvent,
    outcome_path: Path,
    action: RaceAction,
) -> None:
    lock: RepositoryLock
    operation: Callable[[], object]
    if action == "acquire":
        lock = PauseAfterLeaseReadLock(
            state_directory,
            owner_token=owner_token,
            lease_read=transition_reached,
            contender_finished=contender_finished,
        )
        operation = lock.acquire
    else:
        lock = PauseAfterOwnerCheckLock(
            state_directory,
            owner_token=owner_token,
            owner_checked=transition_reached,
            contender_finished=contender_finished,
        )
        operation = lock.release if action == "release" else lock.heartbeat
    record_lock_action(operation, outcome_path)


def acquire_lock(
    state_directory: Path,
    owner_token: str,
    outcome_path: Path,
    finished: ProcessEvent,
) -> None:
    lock = RepositoryLock(
        state_directory,
        timeout_seconds=10,
        now=lambda: 111.0,
        owner_token=owner_token,
    )
    record_lock_action(lock.acquire, outcome_path, finished)


def stop_processes(processes: Sequence[BaseProcess]) -> None:
    for process in processes:
        if process.pid is None:
            continue
        process.join(timeout=PROCESS_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=PROCESS_TIMEOUT_SECONDS)


def assert_process_completed(process: BaseProcess) -> None:
    process.join(timeout=PROCESS_TIMEOUT_SECONDS)
    assert not process.is_alive()
    assert process.exitcode == 0


def run_lease_race(tmp_path: Path, action: RaceAction) -> list[str]:
    state_directory = tmp_path / "state"
    predecessor = RepositoryLock(
        state_directory,
        timeout_seconds=10,
        now=lambda: 100.0,
        owner_token="predecessor",
    )
    predecessor.acquire()
    context = multiprocessing.get_context("fork")
    transition_reached = context.Event()
    contender_finished = context.Event()
    first_outcome = tmp_path / "first-outcome"
    second_outcome = tmp_path / "second-outcome"
    first_owner = "first-contender" if action == "acquire" else "predecessor"
    first = context.Process(
        target=run_paused_action,
        args=(
            state_directory,
            first_owner,
            transition_reached,
            contender_finished,
            first_outcome,
            action,
        ),
    )
    second = context.Process(
        target=acquire_lock,
        args=(
            state_directory,
            "second-contender",
            second_outcome,
            contender_finished,
        ),
    )
    processes = [first, second]

    try:
        first.start()
        assert transition_reached.wait(PROCESS_TIMEOUT_SECONDS)
        second.start()
        assert_process_completed(second)
        assert_process_completed(first)
        return [
            first_outcome.read_text(encoding="utf-8"),
            second_outcome.read_text(encoding="utf-8"),
        ]
    finally:
        stop_processes(processes)


def create_lock(
    state_directory: Path,
    clock: Clock,
    owner_token: str,
) -> RepositoryLock:
    return RepositoryLock(
        state_directory,
        timeout_seconds=10,
        now=clock.now,
        owner_token=owner_token,
    )


def test_owner_acquires_and_releases_a_repository_lease(tmp_path: Path) -> None:
    clock = Clock(100)
    lock = create_lock(tmp_path / "state", clock, "first-owner")

    lock.acquire()

    assert lock.path.exists()
    lock.release()
    assert not lock.path.exists()


def test_second_owner_is_rejected_while_the_lease_is_live(tmp_path: Path) -> None:
    clock = Clock(100)
    first_lock = create_lock(tmp_path / "state", clock, "first-owner")
    second_lock = create_lock(tmp_path / "state", clock, "second-owner")
    first_lock.acquire()

    with pytest.raises(LockUnavailableError, match="active"):
        second_lock.acquire()


def test_only_the_acquired_owner_can_renew_or_release(tmp_path: Path) -> None:
    clock = Clock(100)
    first_lock = create_lock(tmp_path / "state", clock, "first-owner")
    other_lock = create_lock(tmp_path / "state", clock, "other-owner")
    first_lock.acquire()

    with pytest.raises(LockOwnershipError, match="owner"):
        other_lock.heartbeat()
    with pytest.raises(LockOwnershipError, match="owner"):
        other_lock.release()

    assert first_lock.path.exists()


def test_heartbeat_keeps_a_lease_from_becoming_stale(tmp_path: Path) -> None:
    clock = Clock(100)
    first_lock = create_lock(tmp_path / "state", clock, "first-owner")
    second_lock = create_lock(tmp_path / "state", clock, "second-owner")
    first_lock.acquire()
    clock.value = 109
    first_lock.heartbeat()
    clock.value = 118

    with pytest.raises(LockUnavailableError, match="active"):
        second_lock.acquire()


def test_new_owner_reclaims_expired_lease_and_invalidates_previous_owner(
    tmp_path: Path,
) -> None:
    clock = Clock(100)
    first_lock = create_lock(tmp_path / "state", clock, "first-owner")
    second_lock = create_lock(tmp_path / "state", clock, "second-owner")
    first_lock.acquire()
    clock.value = 111

    second_lock.acquire()

    with pytest.raises(LockOwnershipError, match="owner"):
        first_lock.heartbeat()
    with pytest.raises(LockOwnershipError, match="owner"):
        first_lock.release()
    second_lock.release()
    assert not second_lock.path.exists()


def test_concurrent_stale_reclamation_reports_only_one_acquired_lease(
    tmp_path: Path,
) -> None:
    assert sorted(run_lease_race(tmp_path, "acquire")) == ["success", "unavailable"]


def test_release_and_successor_acquisition_cannot_both_succeed(
    tmp_path: Path,
) -> None:
    assert sorted(run_lease_race(tmp_path, "release")) == ["success", "unavailable"]


def test_heartbeat_and_successor_acquisition_cannot_both_succeed(
    tmp_path: Path,
) -> None:
    assert sorted(run_lease_race(tmp_path, "heartbeat")) == [
        "success",
        "unavailable",
    ]


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), float("inf"), -float("inf"), 0.0, -1.0],
    ids=["nan", "positive-infinity", "negative-infinity", "zero", "negative"],
)
def test_nonfinite_or_nonpositive_timeout_is_rejected(
    tmp_path: Path,
    timeout_seconds: float,
) -> None:
    with pytest.raises(ValueError):
        RepositoryLock(tmp_path / "state", timeout_seconds=timeout_seconds)


@pytest.mark.parametrize("timeout_seconds", [True, False])
@pytest.mark.parametrize("existing_lease", [True, False])
def test_boolean_timeout_is_rejected_without_changing_state(
    tmp_path: Path, timeout_seconds: bool, existing_lease: bool
) -> None:
    state = tmp_path / "state"
    owner = RepositoryLock(state, timeout_seconds=10, now=lambda: 100.0)
    if existing_lease:
        owner.acquire()
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError):
        RepositoryLock(state, timeout_seconds=timeout_seconds, now=lambda: 102.0)

    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
    if existing_lease:
        owner.heartbeat()
        owner.release()
    else:
        assert not state.exists()


@pytest.mark.parametrize("action", ["acquire", "heartbeat", "release"])
@pytest.mark.parametrize(
    ("owner_token", "heartbeat_at"),
    [
        pytest.param("owner", float("nan"), id="nan"),
        pytest.param("owner", float("inf"), id="positive-infinity"),
        pytest.param("owner", -float("inf"), id="negative-infinity"),
        pytest.param("owner", 10**1000, id="positive-overflowing-integer"),
        pytest.param("owner", -(10**1000), id="negative-overflowing-integer"),
        pytest.param("", 0, id="empty-owner"),
    ],
)
def test_invalid_persisted_lease_metadata_is_preserved(
    tmp_path: Path, action: str, owner_token: str, heartbeat_at: int | float
) -> None:
    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, now=lambda: 100.0, owner_token="owner"
    )
    lock.acquire()
    metadata_path = lock.path / "lease.json"
    contents = (
        json.dumps(
            {"owner_token": owner_token, "heartbeat_at": heartbeat_at}, indent=2
        ).encode()
        + b"\n"
    )
    metadata_path.write_bytes(contents)

    with pytest.raises(LockUnavailableError, match="metadata is invalid"):
        getattr(lock, action)()

    assert lock.path.is_dir()
    assert set(lock.path.iterdir()) == {metadata_path}
    assert metadata_path.read_bytes() == contents


@pytest.mark.parametrize("heartbeat_at", [100, 100.0], ids=["integer", "float"])
def test_finite_persisted_lease_metadata_retains_stale_takeover(
    tmp_path: Path, heartbeat_at: int | float
) -> None:
    clock = Clock(100)
    first = create_lock(tmp_path / "state", clock, "first-owner")
    first.acquire()
    (first.path / "lease.json").write_text(
        json.dumps({"owner_token": "first-owner", "heartbeat_at": heartbeat_at}),
        encoding="utf-8",
    )
    clock.value = 111
    successor = create_lock(tmp_path / "state", clock, "successor")

    lease = successor.acquire()

    assert lease.owner_token == "successor"
    with pytest.raises(LockOwnershipError):
        first.release()
    successor.release()
    assert not successor.path.exists()


@pytest.mark.parametrize("action", ["acquire", "heartbeat", "release"])
def test_parser_limited_heartbeat_is_refused_without_changing_lease(
    tmp_path: Path, action: str
) -> None:
    digit_limit = sys.get_int_max_str_digits()
    if digit_limit == 0:
        pytest.skip("interpreter integer-string digit limit is disabled")
    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, now=lambda: 100.0, owner_token="owner"
    )
    lock.acquire()
    metadata_path = lock.path / "lease.json"
    contents = (
        b'{"owner_token":"owner","heartbeat_at":' + b"9" * (digit_limit + 1) + b"}\n"
    )
    metadata_path.write_bytes(contents)

    with pytest.raises(LockUnavailableError):
        getattr(lock, action)()

    assert lock.path.is_dir()
    assert set(lock.path.iterdir()) == {metadata_path}
    assert metadata_path.read_bytes() == contents


@pytest.mark.parametrize("owner_token", ["", True, False, 0, 1, b"owner", [], {}])
@pytest.mark.parametrize("existing_lease", [False, True])
def test_invalid_owner_token_is_rejected_before_state_mutation(
    tmp_path: Path, owner_token: object, existing_lease: bool
) -> None:
    state = tmp_path / "state"
    owner = RepositoryLock(state, timeout_seconds=10, owner_token="valid-owner")
    if existing_lease:
        owner.acquire()
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="owner_token"):
        RepositoryLock(state, timeout_seconds=10, owner_token=owner_token)  # type: ignore[arg-type]

    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
    if existing_lease:
        owner.heartbeat()
        owner.release()
    else:
        assert not state.exists()


def test_default_owner_token_supports_lease_lifecycle(tmp_path: Path) -> None:
    lock = RepositoryLock(tmp_path / "state", timeout_seconds=10, owner_token=None)
    lease = lock.acquire()
    assert isinstance(lease.owner_token, str) and lease.owner_token
    assert lock.heartbeat().owner_token == lease.owner_token
    lock.release()
    assert not lock.path.exists()


@pytest.mark.parametrize("action", ["create", "heartbeat", "expiry"])
@pytest.mark.parametrize(
    "sample",
    [float("nan"), float("inf"), -float("inf"), True, False, None, "100", 10**1000],
)
def test_invalid_clock_samples_preserve_healthy_leases_or_leave_no_new_lock(
    tmp_path: Path, action: str, sample: object
) -> None:
    current: object = 100.0

    def now() -> float:
        return current  # type: ignore[return-value]

    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, now=now, owner_token="owner"
    )
    if action != "create":
        lock.acquire()
        before = (lock.path / "lease.json").read_bytes()
    current = sample
    with pytest.raises(ValueError, match="clock"):
        (lock.heartbeat if action == "heartbeat" else lock.acquire)()
    if action == "create":
        assert not lock.path.exists()
    else:
        assert (lock.path / "lease.json").read_bytes() == before
        lock.release()


@pytest.mark.parametrize("action", ["create", "heartbeat", "expiry"])
@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_clock_exceptions_preserve_original_failure_and_lock_state(
    tmp_path: Path, action: str, failure_type: type[BaseException]
) -> None:
    failure = failure_type("clock failed")
    fail = False

    def now() -> float:
        if fail:
            raise failure
        return 100.0

    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, now=now, owner_token="owner"
    )
    if action != "create":
        lock.acquire()
        before = (lock.path / "lease.json").read_bytes()
    fail = True
    with pytest.raises(failure_type) as raised:
        (lock.heartbeat if action == "heartbeat" else lock.acquire)()
    assert raised.value is failure
    if action == "create":
        assert not lock.path.exists()
    else:
        assert (lock.path / "lease.json").read_bytes() == before
        lock.release()


@pytest.mark.parametrize("action", ["acquire", "heartbeat", "release"])
def test_fifo_lease_is_rejected_without_waiting_for_a_writer(
    tmp_path: Path, action: str
) -> None:
    lock = RepositoryLock(tmp_path / "state", timeout_seconds=10, owner_token="owner")
    lock.acquire()
    metadata = lock.path / "lease.json"
    metadata.unlink()
    os.mkfifo(metadata)
    script = (
        "from agent_harness.lock import RepositoryLock, LockUnavailableError\n"
        f"lock = RepositoryLock({str(tmp_path / 'state')!r}, timeout_seconds=10, owner_token='owner')\n"
        "try:\n"
        f"    lock.{action}()\n"
        "except LockUnavailableError:\n    pass\n"
        "else:\n    raise RuntimeError('unsafe lease accepted')\n"
    )
    result = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, timeout=1
    )
    assert result.returncode == 0, result.stderr.decode()
    assert metadata.exists()


@pytest.mark.parametrize("action", ["acquire", "heartbeat", "release"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "oversized"])
def test_unsafe_lease_inputs_are_rejected_without_external_mutation(
    tmp_path: Path, action: str, kind: str
) -> None:
    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, now=lambda: 100.0, owner_token="owner"
    )
    lock.acquire()
    metadata = lock.path / "lease.json"
    original = metadata.read_bytes()
    external = tmp_path / "external.json"
    external.write_bytes(original)
    metadata.unlink()
    if kind == "symlink":
        metadata.symlink_to(external)
    elif kind == "hardlink":
        metadata.hardlink_to(external)
    elif kind == "directory":
        metadata.mkdir()
    else:
        metadata.write_bytes(original + b" " * (1024 * 1024))
    with pytest.raises(LockUnavailableError, match="metadata is invalid"):
        getattr(lock, action)()
    assert lock.path.exists()
    assert external.read_bytes() == original
    assert metadata.lstat()


def test_unsafe_opened_lease_descriptor_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = RepositoryLock(tmp_path / "state", timeout_seconds=10, owner_token="owner")
    lock.acquire()
    metadata = lock.path / "lease.json"
    (tmp_path / "alias").hardlink_to(metadata)
    opened: list[int] = []
    actual_open = os.open

    def record_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        descriptor = actual_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == metadata:
            opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", record_open)
    with pytest.raises(LockUnavailableError):
        lock.release()
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_oversized_owner_metadata_cannot_create_an_unreadable_lease(
    tmp_path: Path,
) -> None:
    lock = RepositoryLock(
        tmp_path / "state", timeout_seconds=10, owner_token="owner" * 100000
    )
    with pytest.raises(ValueError, match="metadata"):
        lock.acquire()
    assert not lock.path.exists()

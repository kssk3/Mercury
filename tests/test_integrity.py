from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from hashlib import sha256
from os import mkfifo, stat_result
from pathlib import Path
from stat import S_IMODE
from typing import BinaryIO

import pytest

from agent_harness import integrity
from agent_harness.integrity import (
    ProtectedInputCheck,
    ProtectedInputFingerprint,
    ProtectedInputState,
    capture_protected_inputs,
    verify_protected_inputs,
)


def test_capture_returns_frozen_fingerprints_in_declared_order(tmp_path: Path) -> None:
    first_path = "tests/first.py"
    second_path = "policy/verification.toml"
    first_bytes = b"assert first\n"
    second_bytes = b"timeout = 30\n"
    for relative_path, contents in (
        (first_path, first_bytes),
        (second_path, second_bytes),
    ):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)

    fingerprints = capture_protected_inputs(tmp_path, (first_path, second_path))

    assert fingerprints == (
        ProtectedInputFingerprint(
            first_path,
            sha256(first_bytes).hexdigest(),
            S_IMODE((tmp_path / first_path).stat().st_mode),
        ),
        ProtectedInputFingerprint(
            second_path,
            sha256(second_bytes).hexdigest(),
            S_IMODE((tmp_path / second_path).stat().st_mode),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        fingerprints[0].path = "tests/replaced.py"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("after_capture", "expected_state"),
    [
        ("unchanged", ProtectedInputState.UNCHANGED),
        ("modified", ProtectedInputState.MODIFIED),
        ("missing", ProtectedInputState.MISSING),
    ],
)
def test_comparison_classifies_later_file_state(
    tmp_path: Path,
    after_capture: str,
    expected_state: ProtectedInputState,
) -> None:
    relative_path = "policy/verification.toml"
    protected_file = tmp_path / relative_path
    protected_file.parent.mkdir()
    protected_file.write_bytes(b"expected exit = 0\n")
    fingerprints = capture_protected_inputs(tmp_path, (relative_path,))
    if after_capture == "modified":
        protected_file.write_bytes(b"expected exit = 1\n")
    elif after_capture == "missing":
        protected_file.unlink()

    checks = verify_protected_inputs(tmp_path, fingerprints)

    assert checks == (ProtectedInputCheck(relative_path, expected_state),)


def test_capture_rejects_unsafe_and_non_regular_inputs(tmp_path: Path) -> None:
    regular_file = tmp_path / "tests/regular.py"
    regular_file.parent.mkdir()
    regular_file.write_text("assert True\n")
    (tmp_path / "directory").mkdir()
    (tmp_path / "link.py").symlink_to(regular_file)

    for paths in (
        ("",),
        ("/absolute",),
        ("../outside",),
        ("tests/../regular.py",),
        ("tests\\regular.py",),
        ("tests/regular.py", "tests/regular.py"),
        ("directory",),
        ("link.py",),
    ):
        with pytest.raises(ValueError):
            capture_protected_inputs(tmp_path, paths)


@pytest.mark.parametrize("replacement", ("symlink", "directory"))
def test_comparison_marks_later_non_regular_paths_unverifiable(
    tmp_path: Path, replacement: str
) -> None:
    relative_path = "tests/protected.py"
    protected_file = tmp_path / relative_path
    protected_file.parent.mkdir()
    protected_file.write_text("assert True\n")
    fingerprints = capture_protected_inputs(tmp_path, (relative_path,))
    protected_file.unlink()
    if replacement == "symlink":
        protected_file.symlink_to(tmp_path / "replacement.py")
    else:
        protected_file.mkdir()

    checks = verify_protected_inputs(tmp_path, fingerprints)

    assert checks == (
        ProtectedInputCheck(relative_path, ProtectedInputState.UNVERIFIABLE),
    )


def test_capture_rejects_file_reached_through_external_symlink(tmp_path: Path) -> None:
    outside_directory = tmp_path.parent / "outside-protected-input"
    outside_directory.mkdir(exist_ok=True)
    (outside_directory / "policy.toml").write_text("timeout = 30\n")
    (tmp_path / "linked-policy").symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(ValueError):
        capture_protected_inputs(tmp_path, ("linked-policy/policy.toml",))


def test_comparison_reports_modified_protected_test_file(tmp_path: Path) -> None:
    relative_path = "tests/test_acceptance.py"
    protected_test = tmp_path / relative_path
    protected_test.parent.mkdir()
    protected_test.write_text("def test_acceptance(): assert False\n")
    fingerprints = capture_protected_inputs(tmp_path, (relative_path,))
    protected_test.write_text("def test_acceptance(): assert True\n")

    assert verify_protected_inputs(tmp_path, fingerprints) == (
        ProtectedInputCheck(relative_path, ProtectedInputState.MODIFIED),
    )


def test_comparison_contains_intermediate_symlink_loop_and_continues(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "protected"
    directory.mkdir()
    (directory / "input.txt").write_text("original")
    (tmp_path / "later.txt").write_text("unaffected")
    fingerprints = capture_protected_inputs(
        tmp_path, ("protected/input.txt", "later.txt")
    )
    directory.rename(tmp_path / "preserved")
    directory.symlink_to("protected", target_is_directory=True)

    checks = verify_protected_inputs(tmp_path, fingerprints)

    assert checks == (
        ProtectedInputCheck("protected/input.txt", ProtectedInputState.UNVERIFIABLE),
        ProtectedInputCheck("later.txt", ProtectedInputState.UNCHANGED),
    )


def test_comparison_contains_permission_failure_and_preserves_later_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    denied = tmp_path / "denied.txt"
    later = tmp_path / "later.txt"
    missing = tmp_path / "missing.txt"
    for target in (denied, later, missing):
        target.write_text("captured")
    fingerprints = capture_protected_inputs(
        tmp_path, ("denied.txt", "later.txt", "missing.txt")
    )
    missing.unlink()
    original_stat = Path.stat

    def denied_stat(path: Path, *, follow_symlinks: bool = True) -> stat_result:
        if path == denied:
            raise PermissionError("unreadable ancestor")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied_stat)

    checks = verify_protected_inputs(tmp_path, fingerprints)

    assert checks == (
        ProtectedInputCheck("denied.txt", ProtectedInputState.UNVERIFIABLE),
        ProtectedInputCheck("later.txt", ProtectedInputState.UNCHANGED),
        ProtectedInputCheck("missing.txt", ProtectedInputState.MISSING),
    )


@pytest.mark.parametrize(
    ("mutation", "expected_state"),
    [
        ("unchanged", ProtectedInputState.UNCHANGED),
        ("added", ProtectedInputState.MODIFIED),
        ("removed", ProtectedInputState.MODIFIED),
        ("modified", ProtectedInputState.MODIFIED),
    ],
)
def test_directory_comparison_detects_file_membership_and_content_changes(
    tmp_path: Path, mutation: str, expected_state: ProtectedInputState
) -> None:
    directory = tmp_path / "tests"
    nested = directory / "nested"
    nested.mkdir(parents=True)
    protected = nested / "test_acceptance.py"
    protected.write_text("def test_acceptance(): assert True\n")
    captured = integrity.capture_protected_directories(tmp_path, ("tests",))
    if mutation == "added":
        (directory / "conftest.py").write_text("# new verification input\n")
    elif mutation == "removed":
        protected.unlink()
    elif mutation == "modified":
        protected.write_text("def test_acceptance(): assert False\n")

    assert integrity.verify_protected_directories(tmp_path, captured) == (
        ProtectedInputCheck("tests", expected_state),
    )


def test_empty_directory_capture_is_read_only_and_detects_first_file(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    captured = integrity.capture_protected_directories(tmp_path, ("tests",))

    assert captured[0].files == ()
    assert tuple(directory.iterdir()) == ()
    assert integrity.verify_protected_directories(tmp_path, captured) == (
        ProtectedInputCheck("tests", ProtectedInputState.UNCHANGED),
    )
    (directory / "first.py").write_bytes(b"first")
    assert integrity.verify_protected_directories(tmp_path, captured) == (
        ProtectedInputCheck("tests", ProtectedInputState.MODIFIED),
    )


@pytest.mark.parametrize(
    "unsafe",
    (
        "missing",
        "file",
        "root_link",
        "ancestor_link",
        "file_link",
        "directory_link",
        "dangling_link",
        "fifo",
        "unsafe_name",
    ),
)
def test_directory_capture_rejects_unsafe_path_or_tree(
    tmp_path: Path, unsafe: str
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_safe.py").write_bytes(b"safe")
    protected_path = "tests"
    if unsafe == "missing":
        protected_path = "absent"
    elif unsafe == "file":
        protected_path = "tests/test_safe.py"
    elif unsafe == "root_link":
        (tmp_path / "alias").symlink_to(directory, target_is_directory=True)
        protected_path = "alias"
    elif unsafe == "ancestor_link":
        nested = directory / "nested"
        nested.mkdir()
        (tmp_path / "alias").symlink_to(directory, target_is_directory=True)
        protected_path = "alias/nested"
    elif unsafe == "file_link":
        (directory / "link.py").symlink_to(directory / "test_safe.py")
    elif unsafe == "directory_link":
        (directory / "linked").symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "dangling_link":
        (directory / "dangling").symlink_to("absent")
    elif unsafe == "fifo":
        mkfifo(directory / "pipe")
    elif unsafe == "unsafe_name":
        (directory / " bad.py").write_bytes(b"unsafe")

    with pytest.raises(ValueError):
        integrity.capture_protected_directories(tmp_path, (protected_path,))


@pytest.mark.parametrize(
    ("replacement", "expected_state"),
    [
        ("missing", ProtectedInputState.MISSING),
        ("file", ProtectedInputState.UNVERIFIABLE),
        ("external_link", ProtectedInputState.UNVERIFIABLE),
        ("internal_link", ProtectedInputState.UNVERIFIABLE),
        ("loop", ProtectedInputState.UNVERIFIABLE),
        ("new_link", ProtectedInputState.UNVERIFIABLE),
        ("new_fifo", ProtectedInputState.UNVERIFIABLE),
    ],
)
def test_directory_comparison_contains_unsafe_replacements_and_continues(
    tmp_path: Path, replacement: str, expected_state: ProtectedInputState
) -> None:
    directory = tmp_path / "protected"
    nested = directory / "tests"
    nested.mkdir(parents=True)
    (nested / "test_safe.py").write_bytes(b"safe")
    (tmp_path / "later").mkdir()
    captured = integrity.capture_protected_directories(
        tmp_path, ("protected/tests", "later")
    )
    if replacement == "missing":
        (nested / "test_safe.py").unlink()
        nested.rmdir()
    elif replacement == "new_link":
        (nested / "link.py").symlink_to(nested / "test_safe.py")
    elif replacement == "new_fifo":
        mkfifo(nested / "pipe")
    else:
        directory.rename(tmp_path / "preserved")
        if replacement == "file":
            directory.write_bytes(b"replacement")
        elif replacement == "external_link":
            directory.symlink_to(tmp_path.parent, target_is_directory=True)
        elif replacement == "internal_link":
            directory.symlink_to(tmp_path / "preserved", target_is_directory=True)
        elif replacement == "loop":
            directory.symlink_to("protected", target_is_directory=True)

    assert integrity.verify_protected_directories(tmp_path, captured) == (
        ProtectedInputCheck("protected/tests", expected_state),
        ProtectedInputCheck("later", ProtectedInputState.UNCHANGED),
    )


@pytest.mark.parametrize("operation", ("lstat", "iterdir", "open"))
def test_directory_inspection_contains_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    denied_file = directory / "test_safe.py"
    denied_file.write_bytes(b"safe")
    (tmp_path / "later").mkdir()
    captured = integrity.capture_protected_directories(tmp_path, ("tests", "later"))
    boundary = os if operation == "open" else Path
    original = getattr(boundary, operation)
    denied_path = denied_file if operation == "open" else directory

    def fail_on_denied(path: Path, *args: object, **kwargs: object) -> object:
        if path == denied_path:
            raise PermissionError("denied inspection")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(boundary, operation, fail_on_denied)

    with pytest.raises(ValueError):
        integrity.capture_protected_directories(tmp_path, ("tests",))
    assert integrity.verify_protected_directories(tmp_path, captured) == (
        ProtectedInputCheck("tests", ProtectedInputState.UNVERIFIABLE),
        ProtectedInputCheck("later", ProtectedInputState.UNCHANGED),
    )


def test_directory_fingerprint_normalizes_and_freezes_file_collection(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "z.py").write_bytes(b"z")
    (directory / "a.py").write_bytes(b"a")
    entries = [
        ProtectedInputFingerprint(
            "z.py",
            sha256(b"z").hexdigest(),
            S_IMODE((directory / "z.py").stat().st_mode),
        ),
        ProtectedInputFingerprint(
            "a.py",
            sha256(b"a").hexdigest(),
            S_IMODE((directory / "a.py").stat().st_mode),
        ),
    ]
    fingerprint = integrity.ProtectedDirectoryFingerprint("tests", entries)  # type: ignore[arg-type]
    entries.clear()

    assert fingerprint.files == (
        ProtectedInputFingerprint(
            "a.py",
            sha256(b"a").hexdigest(),
            S_IMODE((directory / "a.py").stat().st_mode),
        ),
        ProtectedInputFingerprint(
            "z.py",
            sha256(b"z").hexdigest(),
            S_IMODE((directory / "z.py").stat().st_mode),
        ),
    )
    assert integrity.capture_protected_directories(tmp_path, ["tests"]) == (
        fingerprint,
    )
    assert integrity.verify_protected_directories(tmp_path, [fingerprint]) == (
        ProtectedInputCheck("tests", ProtectedInputState.UNCHANGED),
    )
    with pytest.raises(FrozenInstanceError):
        fingerprint.path = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize(
    "paths",
    [
        "tests",
        ("tests", "tests"),
        ("../outside",),
        ("/absolute",),
        ("tests/../other",),
        ("tests\\nested",),
    ],
)
def test_directory_capture_rejects_ambiguous_or_unsafe_paths(
    tmp_path: Path, paths: object
) -> None:
    (tmp_path / "tests").mkdir()
    with pytest.raises(ValueError):
        integrity.capture_protected_directories(tmp_path, paths)  # type: ignore[arg-type]


def test_directory_fingerprint_rejects_duplicate_or_wrong_type_files() -> None:
    entry = ProtectedInputFingerprint("safe.py", sha256(b"safe").hexdigest())
    for files in ((entry, entry), (object(),), "safe.py"):
        with pytest.raises(ValueError):
            integrity.ProtectedDirectoryFingerprint("tests", files)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        integrity.ProtectedDirectoryFingerprint("../outside", ())


def test_directory_verification_rejects_ambiguous_fingerprints(tmp_path: Path) -> None:
    fingerprint = integrity.ProtectedDirectoryFingerprint("tests", ())
    for captured in (fingerprint, (fingerprint, fingerprint), (object(),), "tests"):
        with pytest.raises(ValueError):
            integrity.verify_protected_directories(tmp_path, captured)  # type: ignore[arg-type]


def test_directory_verification_contains_root_resolution_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "policy").mkdir()
    captured = integrity.capture_protected_directories(tmp_path, ("tests", "policy"))
    loop = tmp_path / "loop"
    loop.symlink_to("loop", target_is_directory=True)

    with pytest.raises(ValueError):
        integrity.capture_protected_directories(loop, ("tests",))
    assert integrity.verify_protected_directories(loop, captured) == (
        ProtectedInputCheck("tests", ProtectedInputState.UNVERIFIABLE),
        ProtectedInputCheck("policy", ProtectedInputState.UNVERIFIABLE),
    )


@pytest.mark.parametrize(
    "operation",
    ["capture_file", "verify_file", "capture_directory", "verify_directory"],
)
def test_protected_hashing_streams_binary_files_in_bounded_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    directory = tmp_path / "protected"
    directory.mkdir()
    target = directory / "binary.dat"
    contents = bytes(range(256)) * 4096 + b"\x00\xfffinal-block"
    target.write_bytes(contents)
    digest = sha256(contents).hexdigest()
    file_fingerprint = ProtectedInputFingerprint(
        "protected/binary.dat", digest, S_IMODE(target.stat().st_mode)
    )
    directory_fingerprint = integrity.ProtectedDirectoryFingerprint(
        "protected",
        (
            ProtectedInputFingerprint(
                "binary.dat", digest, S_IMODE(target.stat().st_mode)
            ),
        ),
    )
    original_open = os.fdopen
    read_sizes: list[int] = []

    class BoundedReader:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream

        def fileno(self) -> int:
            return self.stream.fileno()

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 256 * 1024, "hashing must use bounded reads"
            read_sizes.append(size)
            return self.stream.read(size)

    @contextmanager
    def bounded_open(descriptor: int, mode: str = "r") -> Iterator[BoundedReader]:
        assert mode == "rb"
        with original_open(descriptor, "rb") as stream:
            yield BoundedReader(stream)

    monkeypatch.setattr(os, "fdopen", bounded_open)
    if operation == "capture_file":
        assert capture_protected_inputs(tmp_path, ("protected/binary.dat",)) == (
            file_fingerprint,
        )
    elif operation == "capture_directory":
        assert integrity.capture_protected_directories(tmp_path, ("protected",)) == (
            directory_fingerprint,
        )
    else:

        def verify() -> tuple[ProtectedInputCheck, ...]:
            if operation == "verify_file":
                return verify_protected_inputs(tmp_path, (file_fingerprint,))
            return integrity.verify_protected_directories(
                tmp_path, (directory_fingerprint,)
            )

        assert verify()[0].state is ProtectedInputState.UNCHANGED
        with target.open("ab") as stream:
            stream.write(b"changed")
        assert verify()[0].state is ProtectedInputState.MODIFIED
    assert len(read_sizes) > 1


@pytest.mark.parametrize("directory_mode", [False, True])
def test_stream_read_failure_preserves_error_classification_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory_mode: bool
) -> None:
    denied_directory = tmp_path / "denied"
    later_directory = tmp_path / "later"
    denied_directory.mkdir()
    later_directory.mkdir()
    denied = denied_directory / "input.bin"
    denied.write_bytes(b"x" * (1024 * 1024))
    (later_directory / "input.bin").write_bytes(b"unaffected")
    paths = (
        ("denied", "later")
        if directory_mode
        else ("denied/input.bin", "later/input.bin")
    )
    file_capture = capture_protected_inputs(
        tmp_path, ("denied/input.bin", "later/input.bin")
    )
    directory_capture = integrity.capture_protected_directories(
        tmp_path, ("denied", "later")
    )
    original_open = os.fdopen

    class FailingReader:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream
            self.first_read = True

        def fileno(self) -> int:
            return self.stream.fileno()

        def read(self, size: int = -1) -> bytes:
            if not self.first_read:
                raise OSError("device failed during read")
            self.first_read = False
            return self.stream.read(size)

    @contextmanager
    def failing_open(
        descriptor: int, mode: str = "r"
    ) -> Iterator[BinaryIO | FailingReader]:
        assert mode == "rb"
        with original_open(descriptor, "rb") as stream:
            yield (
                FailingReader(stream)
                if os.fstat(descriptor).st_ino == denied.stat().st_ino
                else stream
            )

    monkeypatch.setattr(os, "fdopen", failing_open)
    with pytest.raises(ValueError):
        if directory_mode:
            integrity.capture_protected_directories(tmp_path, paths)
        else:
            capture_protected_inputs(tmp_path, paths)
    checks = (
        integrity.verify_protected_directories(tmp_path, directory_capture)
        if directory_mode
        else verify_protected_inputs(tmp_path, file_capture)
    )
    assert checks == (
        ProtectedInputCheck(paths[0], ProtectedInputState.UNVERIFIABLE),
        ProtectedInputCheck(paths[1], ProtectedInputState.UNCHANGED),
    )


@pytest.mark.parametrize("directory", [False, True])
def test_protected_ignored_permission_only_change_is_modified(
    tmp_path: Path, directory: bool
) -> None:
    from test_loop import _repository

    from agent_harness.turn import RepositoryObservation

    root = _repository(tmp_path)
    (root / ".git/info/exclude").write_text("protected/\n")
    protected = root / "protected"
    protected.mkdir()
    target = protected / "private.dat"
    target.write_bytes(b"unchanged private contents")
    target.chmod(0o600)
    before = RepositoryObservation.capture(root)
    captured = (
        integrity.capture_protected_directories(root, ("protected",))
        if directory
        else capture_protected_inputs(root, ("protected/private.dat",))
    )
    target.chmod(0o777)
    assert RepositoryObservation.capture(root) == before
    checks = (
        integrity.verify_protected_directories(root, captured)  # type: ignore[arg-type]
        if directory
        else verify_protected_inputs(root, captured)  # type: ignore[arg-type]
    )
    assert checks[0].state is ProtectedInputState.MODIFIED


@pytest.mark.parametrize("mode", [True, 1.5, -1, 0o10000])
def test_protected_fingerprint_rejects_malformed_mode(mode: object) -> None:
    with pytest.raises(ValueError, match="mode"):
        ProtectedInputFingerprint("safe", "a" * 64, mode)  # type: ignore[arg-type]


def test_legacy_protected_fingerprint_retains_content_only_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "safe"
    target.write_bytes(b"same")
    legacy = ProtectedInputFingerprint("safe", sha256(b"same").hexdigest())
    target.chmod(0o777)
    assert legacy.mode is None
    assert (
        verify_protected_inputs(tmp_path, (legacy,))[0].state
        is ProtectedInputState.UNCHANGED
    )


def test_protected_directory_mode_remains_outside_contents_contract(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "protected"
    directory.mkdir()
    (directory / "safe").write_bytes(b"same")
    captured = integrity.capture_protected_directories(tmp_path, ("protected",))
    directory.chmod(0o777)
    assert (
        integrity.verify_protected_directories(tmp_path, captured)[0].state
        is ProtectedInputState.UNCHANGED
    )


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("operation", ["capture", "verify"])
def test_ignored_protected_hardlink_refused_despite_same_bytes_and_mode(
    tmp_path: Path, directory: bool, operation: str
) -> None:
    from test_loop import _repository

    from agent_harness.turn import RepositoryObservation

    root = _repository(tmp_path)
    (root / ".git/info/exclude").write_text("protected/\n")
    (root / "protected").mkdir()
    target = root / "protected/input"
    target.write_bytes(b"private bytes")
    target.chmod(0o600)
    captured_files = capture_protected_inputs(root, ("protected/input",))
    captured_directories = integrity.capture_protected_directories(root, ("protected",))
    before = RepositoryObservation.capture(root)
    outside = tmp_path / "outside"
    outside.write_bytes(target.read_bytes())
    outside.chmod(0o600)
    target.unlink()
    target.hardlink_to(outside)
    assert target.stat().st_nlink == 2
    assert RepositoryObservation.capture(root) == before
    if operation == "capture":
        with pytest.raises(ValueError):
            if directory:
                integrity.capture_protected_directories(root, ("protected",))
            else:
                capture_protected_inputs(root, ("protected/input",))
    else:
        checks = (
            integrity.verify_protected_directories(root, captured_directories)
            if directory
            else verify_protected_inputs(root, captured_files)
        )
        assert checks[0].state is ProtectedInputState.UNVERIFIABLE
    assert outside.read_bytes() == b"private bytes"


@pytest.mark.parametrize("operation", ["capture", "verify"])
def test_actual_opened_protected_hardlink_is_refused_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    target = tmp_path / "safe"
    target.write_bytes(b"same")
    fingerprints = capture_protected_inputs(tmp_path, ("safe",))
    outside = tmp_path / "outside"
    outside.write_bytes(b"same")
    original_open = os.open
    opened: list[int] = []

    def substituted_open(path: Path, flags: int) -> int:
        assert path == target
        target.unlink()
        target.hardlink_to(outside)
        descriptor = original_open(path, flags)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", substituted_open)
    if operation == "capture":
        with pytest.raises(ValueError):
            capture_protected_inputs(tmp_path, ("safe",))
    else:
        assert (
            verify_protected_inputs(tmp_path, fingerprints)[0].state
            is ProtectedInputState.UNVERIFIABLE
        )
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("operation", ["capture", "verify"])
@pytest.mark.parametrize("ancestor", ["symlink", "regular"])
def test_protected_file_rejects_unsafe_ancestor(
    tmp_path: Path, operation: str, ancestor: str
) -> None:
    directory = tmp_path / "protected"
    directory.mkdir()
    (directory / "input").write_bytes(b"same")
    captured = capture_protected_inputs(tmp_path, ("protected/input",))
    directory.rename(tmp_path / "preserved")
    if ancestor == "symlink":
        directory.symlink_to(tmp_path / "preserved", target_is_directory=True)
    else:
        directory.write_bytes(b"same")
    if operation == "capture":
        with pytest.raises(ValueError):
            capture_protected_inputs(tmp_path, ("protected/input",))
    else:
        assert (
            verify_protected_inputs(tmp_path, captured)[0].state
            is ProtectedInputState.UNVERIFIABLE
        )


@pytest.mark.parametrize("directory_mode", [False, True])
@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_protected_open_rejects_substituted_endpoint_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_mode: bool,
    replacement: str,
) -> None:
    directory = tmp_path / "protected"
    directory.mkdir()
    target = directory / "input"
    target.write_bytes(b"same")
    original_open = os.open
    opened: list[int] = []

    def substitute(path: Path, flags: int) -> int:
        assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
        target.unlink()
        if replacement == "fifo":
            mkfifo(target)
        else:
            target.symlink_to(tmp_path / "absent")
        descriptor = original_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def unsafe_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("protected hashing must open safely before reading")

    monkeypatch.setattr(os, "open", substitute)
    monkeypatch.setattr(Path, "open", unsafe_open)
    with pytest.raises(ValueError):
        if directory_mode:
            integrity.capture_protected_directories(tmp_path, ("protected",))
        else:
            capture_protected_inputs(tmp_path, ("protected/input",))
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)

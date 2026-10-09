from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from os import mkfifo, stat_result
from pathlib import Path

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
        ProtectedInputFingerprint(first_path, sha256(first_bytes).hexdigest()),
        ProtectedInputFingerprint(second_path, sha256(second_bytes).hexdigest()),
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


@pytest.mark.parametrize("operation", ("lstat", "iterdir", "read_bytes"))
def test_directory_inspection_contains_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    denied_file = directory / "test_safe.py"
    denied_file.write_bytes(b"safe")
    (tmp_path / "later").mkdir()
    captured = integrity.capture_protected_directories(tmp_path, ("tests", "later"))
    original = getattr(Path, operation)
    denied_path = denied_file if operation == "read_bytes" else directory

    def fail_on_denied(path: Path) -> object:
        if path == denied_path:
            raise PermissionError("denied inspection")
        return original(path)

    monkeypatch.setattr(Path, operation, fail_on_denied)

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
        ProtectedInputFingerprint("z.py", sha256(b"z").hexdigest()),
        ProtectedInputFingerprint("a.py", sha256(b"a").hexdigest()),
    ]
    fingerprint = integrity.ProtectedDirectoryFingerprint("tests", entries)  # type: ignore[arg-type]
    entries.clear()

    assert fingerprint.files == (
        ProtectedInputFingerprint("a.py", sha256(b"a").hexdigest()),
        ProtectedInputFingerprint("z.py", sha256(b"z").hexdigest()),
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

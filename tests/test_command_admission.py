from __future__ import annotations

import hashlib
import io
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from agent_harness import command_admission as commands
from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "docs").mkdir()
    (root / "docs" / "commands.bin").write_bytes(b"\xff explicit bytes\x00")
    return root


def test_discovery_is_immutable_explicit_attribution_without_execution(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual_run = subprocess.run
    actual_open = Path.open
    calls: list[list[str]] = []
    reads: list[Path] = []

    def git_only(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == [
            "git",
            "-C",
            str(repository / "docs"),
            "rev-parse",
            "--show-toplevel",
        ]
        calls.append(argv)
        return actual_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    def selected_only(path: Path, mode: str = "r", **kwargs: object) -> object:
        assert path == repository / "docs/commands.bin"
        assert mode == "rb"
        reads.append(path)
        return actual_open(path, mode, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", git_only)
    monkeypatch.setattr(Path, "open", selected_only)
    argv = ["sh", "-c", "touch SHOULD_NOT_EXIST", "$(literal)"]
    result = commands.discover_command(
        repository / "docs",
        source_path="docs/commands.bin",
        argv=argv,
        working_directory="docs",
        timeout_seconds=30,
    )
    argv.append("changed")
    assert result.repository_root == str(repository.resolve())
    assert result.source_path == "docs/commands.bin"
    assert (
        result.source_sha256 == hashlib.sha256(b"\xff explicit bytes\x00").hexdigest()
    )
    assert result.argv == ("sh", "-c", "touch SHOULD_NOT_EXIST", "$(literal)")
    assert result.working_directory == "docs"
    assert result.timeout_seconds == 30
    assert len(calls) == len(reads) == 1
    assert not (repository / "SHOULD_NOT_EXIST").exists()
    with pytest.raises(FrozenInstanceError):
        result.timeout_seconds = 1  # type: ignore[misc]


@pytest.fixture
def contract() -> TaskContract:
    return TaskContract("Verify", ("src",), ("tests",), ("Checks pass",))


@pytest.fixture
def candidate(repository: Path) -> commands.DiscoveredCommand:
    return commands.discover_command(
        repository,
        source_path="docs/commands.bin",
        argv=["pytest", "-q"],
        timeout_seconds=30,
    )


def approve(
    candidate: commands.DiscoveredCommand, contract: TaskContract
) -> commands.CommandApproval:
    return commands.record_command_approval(
        candidate, contract, approver="human", approved_at="2026-10-01T12:00:00+09:00"
    )


def test_exact_approval_converts_to_existing_policy(
    repository: Path, candidate: commands.DiscoveredCommand, contract: TaskContract
) -> None:
    approval = approve(candidate, contract)
    assert approval.candidate == candidate
    assert approval.contract_json == contract.to_json()
    assert approval.approver == "human"
    assert approval.approved_at == "2026-10-01T12:00:00+09:00"
    with pytest.raises(FrozenInstanceError):
        approval.approver = "other"  # type: ignore[misc]
    assert commands.admit_command(repository, candidate, contract, approval) == (
        VerificationPolicy((("pytest", "-q"),), ".", 30)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository_root", "/another"),
        ("source_path", "other.txt"),
        ("source_sha256", "0" * 64),
        ("argv", ("pytest", "-x")),
        ("working_directory", "docs"),
        ("timeout_seconds", 31),
    ],
)
def test_approval_rejects_every_changed_identity_field(
    repository: Path,
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
    field: str,
    value: object,
) -> None:
    approval = approve(candidate, contract)
    changed = replace(candidate, **{field: value})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="match"):
        commands.admit_command(repository, changed, contract, approval)


def test_missing_lookalike_and_wrong_contract_approval_rejected(
    repository: Path, candidate: commands.DiscoveredCommand, contract: TaskContract
) -> None:
    for approval in [None, object()]:
        with pytest.raises(TypeError, match="CommandApproval"):
            commands.admit_command(repository, candidate, contract, approval)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="contract"):
        commands.admit_command(
            repository,
            candidate,
            replace(contract, goal="different"),
            approve(candidate, contract),
        )


@pytest.mark.parametrize(
    "approver,time",
    [
        ("", "2026-10-01T00:00:00Z"),
        ("  ", "2026-10-01T00:00:00Z"),
        ("human", "bad"),
        ("human", "2026-10-01T00:00:00"),
    ],
)
def test_approval_provenance_validation(
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
    approver: str,
    time: str,
) -> None:
    with pytest.raises(ValueError):
        commands.record_command_approval(
            candidate, contract, approver=approver, approved_at=time
        )


def test_source_and_repository_are_revalidated(
    tmp_path: Path,
    repository: Path,
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
) -> None:
    approval = approve(candidate, contract)
    other = tmp_path / "other"
    subprocess.run(["git", "init", "--quiet", str(other)], check=True)
    with pytest.raises(ValueError, match="repository"):
        commands.admit_command(other, candidate, contract, approval)
    (repository / candidate.source_path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source"):
        commands.admit_command(repository, candidate, contract, approval)
    (repository / candidate.source_path).unlink()
    with pytest.raises(ValueError, match="source"):
        commands.admit_command(repository, candidate, contract, approval)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", ""),
        ("source_path", "."),
        ("source_path", "/tmp/file"),
        ("source_path", "../file"),
        ("source_path", "docs/../file"),
        ("source_path", "docs//commands.bin"),
        ("source_path", "./docs/commands.bin"),
        ("source_path", "docs/commands.bin/"),
        ("source_path", "bad\x00path"),
        ("working_directory", ""),
        ("working_directory", "/tmp"),
        ("working_directory", "../docs"),
        ("working_directory", "./docs"),
        ("working_directory", "docs/"),
        ("working_directory", "bad\x00path"),
        ("argv", []),
        ("argv", "pytest"),
        ("argv", [""]),
        ("argv", ["pytest", "bad\x00arg"]),
        ("argv", [1]),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", True),
        ("timeout_seconds", 1.5),
    ],
)
def test_discovery_rejects_invalid_inputs(
    repository: Path, field: str, value: object
) -> None:
    kwargs: dict[str, object] = dict(
        source_path="docs/commands.bin", argv=["pytest"], timeout_seconds=30
    )
    kwargs[field] = value
    with pytest.raises(ValueError):
        commands.discover_command(repository, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kind",
    [
        "source_link",
        "source_ancestor",
        "cwd_link",
        "cwd_ancestor",
        "source_dir",
        "missing_cwd",
        "file_cwd",
    ],
)
@pytest.mark.parametrize("conversion", [False, True])
def test_filesystem_boundaries(
    repository: Path,
    tmp_path: Path,
    contract: TaskContract,
    kind: str,
    conversion: bool,
) -> None:
    (repository / "work").mkdir()
    (repository / "work/sub").mkdir()
    candidate = commands.discover_command(
        repository,
        source_path="docs/commands.bin",
        argv=["pytest"],
        working_directory="work/sub",
        timeout_seconds=30,
    )
    approval = approve(candidate, contract)
    source = repository / "docs/commands.bin"
    if kind == "source_link":
        target = repository / "target"
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
    elif kind == "source_ancestor":
        outside = tmp_path / "outside"
        (repository / "docs").rename(outside)
        (repository / "docs").symlink_to(outside, target_is_directory=True)
    elif kind == "cwd_link":
        (repository / "work/sub").rmdir()
        (repository / "work/sub").symlink_to(
            repository / "docs", target_is_directory=True
        )
    elif kind == "cwd_ancestor":
        (repository / "work").rename(tmp_path / "outside")
        (repository / "work").symlink_to(tmp_path / "outside", target_is_directory=True)
    elif kind == "source_dir":
        source.unlink()
        source.mkdir()
    elif kind == "missing_cwd":
        (repository / "work/sub").rmdir()
    else:
        (repository / "work/sub").rmdir()
        (repository / "work/sub").write_text("file")
    with pytest.raises(ValueError):
        if conversion:
            commands.admit_command(repository, candidate, contract, approval)
        else:
            commands.discover_command(
                repository,
                source_path=candidate.source_path,
                argv=candidate.argv,
                working_directory=candidate.working_directory,
                timeout_seconds=30,
            )


@pytest.mark.parametrize("conversion", [False, True])
def test_unreadable_selected_source_fails_explicitly(
    repository: Path,
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
    monkeypatch: pytest.MonkeyPatch,
    conversion: bool,
) -> None:
    approval = approve(candidate, contract)

    def denied(path: Path, *args: object, **kwargs: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ValueError, match="source"):
        if conversion:
            commands.admit_command(repository, candidate, contract, approval)
        else:
            commands.discover_command(
                repository,
                source_path=candidate.source_path,
                argv=candidate.argv,
                timeout_seconds=30,
            )


def test_direct_records_are_defensive_and_validate_provenance(
    candidate: commands.DiscoveredCommand, contract: TaskContract
) -> None:
    argv = ["pytest"]
    copied = replace(candidate, argv=argv)  # type: ignore[arg-type]
    approval = approve(copied, contract)
    argv.append("changed")
    assert copied.argv == ("pytest",)
    assert approval.candidate.argv == ("pytest",)
    with pytest.raises(ValueError):
        replace(approval, approver=" ")
    with pytest.raises(ValueError):
        replace(approval, approved_at="2026-10-01T00:00:00")


@pytest.mark.parametrize("conversion", [False, True])
def test_source_inspection_errors_fail_closed(
    repository: Path,
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
    monkeypatch: pytest.MonkeyPatch,
    conversion: bool,
) -> None:
    original = Path.lstat
    approval = approve(candidate, contract)

    def denied(path: Path, **kwargs: object) -> object:
        if path == repository / candidate.source_path:
            raise PermissionError("denied")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(ValueError, match="source"):
        if conversion:
            commands.admit_command(repository, candidate, contract, approval)
        else:
            commands.discover_command(
                repository,
                source_path=candidate.source_path,
                argv=candidate.argv,
                timeout_seconds=30,
            )


def test_approval_and_conversion_do_not_execute_or_write(
    repository: Path,
    candidate: commands.DiscoveredCommand,
    contract: TaskContract,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        str(path.relative_to(repository)): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }
    actual_run = subprocess.run
    calls: list[list[str]] = []

    def git_only(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == ["git", "-C", str(repository), "rev-parse", "--show-toplevel"]
        calls.append(argv)
        return actual_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", git_only)
    approval = approve(candidate, contract)
    assert calls == []
    commands.admit_command(repository, candidate, contract, approval)
    assert len(calls) == 1
    assert before == {
        str(path.relative_to(repository)): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("conversion", [False, True])
def test_large_source_hashing_uses_bounded_reads_and_preserves_identity(
    repository: Path,
    contract: TaskContract,
    monkeypatch: pytest.MonkeyPatch,
    conversion: bool,
) -> None:
    source = repository / "docs/commands.bin"
    contents = bytes(range(256)) * 8193
    source.write_bytes(contents)
    candidate = commands.DiscoveredCommand(
        str(repository.resolve()),
        "docs/commands.bin",
        hashlib.sha256(contents).hexdigest(),
        ("pytest",),
        ".",
        30,
    )
    approval = approve(candidate, contract)
    actual_open = Path.open

    class BoundedReader(io.BufferedReader):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= 1024 * 1024, (
                "source hashing must use bounded reads"
            )
            return super().read(size)

    def bounded_open(path: Path, mode: str = "r", **kwargs: object) -> object:
        if path == source and mode == "rb":
            return BoundedReader(io.FileIO(path, "r"))
        return actual_open(path, mode, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", bounded_open)
    if conversion:
        assert commands.admit_command(repository, candidate, contract, approval) == (
            VerificationPolicy((("pytest",),), ".", 30)
        )
        source.write_bytes(contents + b"changed")
        with pytest.raises(ValueError, match="source"):
            commands.admit_command(repository, candidate, contract, approval)
    else:
        discovered = commands.discover_command(
            repository,
            source_path="docs/commands.bin",
            argv=["pytest"],
            timeout_seconds=30,
        )
        assert discovered.source_sha256 == candidate.source_sha256

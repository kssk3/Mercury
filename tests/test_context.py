from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_harness.context import ContextPacket, ContextSource, build_context_packet


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "docs").mkdir()
    (root / "docs/a.txt").write_bytes('한글 "quoted"\\\r\n\t\x00'.encode())
    (root / "z.txt").write_bytes(b"")
    return root


def expected_json(root: Path, budget: int, paths: list[str]) -> str:
    return json.dumps(
        dict(
            repository_root=str(root.resolve()),
            budget_bytes=budget,
            sources=[
                dict(
                    source_path=p,
                    source_sha256=hashlib.sha256((root / p).read_bytes()).hexdigest(),
                    size_bytes=len((root / p).read_bytes()),
                    text=(root / p).read_bytes().decode("utf-8"),
                )
                for p in sorted(paths)
            ],
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def test_complete_deterministic_attribution(repository: Path) -> None:
    paths = ["z.txt", "docs/a.txt"]
    packet = build_context_packet(
        repository / "docs", source_paths=paths, budget_bytes=2000
    )
    paths.clear()
    assert packet.to_json() == expected_json(repository, 2000, ["z.txt", "docs/a.txt"])
    assert packet.size_bytes == len(packet.to_json().encode("utf-8"))
    assert packet.size_bytes > sum(s.size_bytes for s in packet.sources)
    assert packet.sources[0].text.endswith("\r\n\t\x00")
    assert packet.sources[1].text == ""
    assert packet == build_context_packet(
        repository, source_paths=["docs/a.txt", "z.txt"], budget_bytes=2000
    )
    with pytest.raises(FrozenInstanceError):
        packet.budget_bytes = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        packet.sources[0].text = ""  # type: ignore[misc]


@pytest.mark.parametrize("paths", [[], ["docs/a.txt"], ["z.txt", "docs/a.txt"]])
def test_exact_complete_json_budget(repository: Path, paths: list[str]) -> None:
    budget = 1000
    while True:
        size = len(expected_json(repository, budget, paths).encode("utf-8"))
        if budget == size:
            break
        budget = size
    packet = build_context_packet(repository, source_paths=paths, budget_bytes=budget)
    assert packet.size_bytes == budget
    with pytest.raises(ValueError, match="budget"):
        build_context_packet(repository, source_paths=paths, budget_bytes=budget - 1)


@pytest.mark.parametrize("source_count", [16, 32])
def test_serialization_work_is_linear_for_empty_sources(
    repository: Path, monkeypatch: pytest.MonkeyPatch, source_count: int
) -> None:
    paths = [f"empty-{index:02}.txt" for index in range(source_count)]
    for path in paths:
        (repository / path).write_bytes(b"")
    expected = expected_json(repository, 100_000, paths)
    actual_dumps = json.dumps
    serialized_bytes = 0

    def observed_dumps(value: object, **kwargs: object) -> str:
        nonlocal serialized_bytes
        result = actual_dumps(value, **kwargs)  # type: ignore[arg-type]
        serialized_bytes += len(result.encode("utf-8"))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(json, "dumps", observed_dumps)
        packet = build_context_packet(
            repository, source_paths=paths, budget_bytes=100_000
        )
    assert packet.to_json() == expected
    assert serialized_bytes <= 4 * len(expected.encode("utf-8"))


def test_multisource_exact_overflow_stops_before_later_read(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    escaped_path = 'z-한글"\\.txt'
    (repository / escaped_path).write_bytes('雪"\\\x00\r\n'.encode())
    later_path = "zz-later.txt"
    (repository / later_path).write_bytes(b"later")
    paths = ["docs/a.txt", escaped_path]
    budget = 1000
    while True:
        size = len(expected_json(repository, budget, paths).encode("utf-8"))
        if budget == size:
            break
        budget = size
    packet = build_context_packet(repository, source_paths=paths, budget_bytes=budget)
    assert packet.to_json() == expected_json(repository, budget, paths)
    assert packet.size_bytes == budget
    actual_open = Path.open
    opened: list[str] = []

    def selected(path: Path, mode: str) -> object:
        relative = str(path.relative_to(repository))
        assert relative != later_path
        opened.append(relative)
        return actual_open(path, mode)

    monkeypatch.setattr(Path, "open", selected)
    with pytest.raises(ValueError, match="budget"):
        build_context_packet(
            repository, source_paths=[later_path, *paths], budget_bytes=budget - 1
        )
    assert opened == sorted(paths)


def test_invalid_utf8_fails(repository: Path) -> None:
    (repository / "z.txt").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        build_context_packet(repository, source_paths=["z.txt"], budget_bytes=1000)


@pytest.mark.parametrize(
    "paths",
    [
        "z.txt",
        None,
        {"z.txt"},
        iter(["z.txt"]),
        ["z.txt", "z.txt"],
        [""],
        ["."],
        ["/tmp/x"],
        ["../x"],
        ["docs/../z.txt"],
        ["./z.txt"],
        ["docs//a.txt"],
        ["z.txt/"],
        ["bad\x00"],
        [1],
    ],
)
def test_invalid_selection_before_read(
    repository: Path, monkeypatch: pytest.MonkeyPatch, paths: object
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("payload opened before lexical validation")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError):
        build_context_packet(repository, source_paths=paths, budget_bytes=2000)  # type: ignore[arg-type]


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "1000", None])
def test_invalid_budget(repository: Path, budget: object) -> None:
    with pytest.raises(ValueError):
        build_context_packet(repository, source_paths=[], budget_bytes=budget)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", "../a"),
        ("source_sha256", "0" * 64),
        ("source_sha256", None),
        ("size_bytes", 2),
        ("size_bytes", True),
        ("size_bytes", -1),
        ("text", 1),
        ("text", "\ud800"),
    ],
)
def test_direct_source_validation(field: str, value: object) -> None:
    data: dict[str, object] = dict(
        source_path="a",
        source_sha256=hashlib.sha256(b"a").hexdigest(),
        size_bytes=1,
        text="a",
    )
    data[field] = value
    with pytest.raises(ValueError):
        ContextSource(**data)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "root", ["relative", "", "/tmp/../a", "/tmp//a", "/tmp/a/", "/tmp/\x00", 1]
)
def test_direct_root_validation(root: object) -> None:
    with pytest.raises(ValueError):
        ContextPacket(root, 2000, ())  # type: ignore[arg-type]


@pytest.mark.parametrize("root", ["//", "//tmp"])
def test_direct_root_rejects_double_leading_separator(root: str) -> None:
    with pytest.raises(ValueError, match="canonical and absolute"):
        ContextPacket(root, 2000, ())


def test_direct_root_validation_needs_no_filesystem_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("direct packet root validation consulted the filesystem")

    for operation in ("stat", "lstat", "open", "resolve"):
        monkeypatch.setattr(Path, operation, forbidden)
    packet = ContextPacket("/e3-canonical-root-without-existence-requirement", 2000, ())
    assert packet.repository_root == "/e3-canonical-root-without-existence-requirement"


@pytest.mark.parametrize("sources", ["bad", [object()], None])
def test_direct_sources_validation(sources: object) -> None:
    with pytest.raises(ValueError):
        ContextPacket("/tmp", 2000, sources)  # type: ignore[arg-type]


def test_direct_packet_copies_sorts_and_rejects_duplicate_sources(
    repository: Path,
) -> None:
    packet = build_context_packet(
        repository, source_paths=["z.txt", "docs/a.txt"], budget_bytes=2000
    )
    sources = list(reversed(packet.sources))
    direct = ContextPacket(packet.repository_root, 2000, sources)  # type: ignore[arg-type]
    sources.clear()
    assert direct == packet
    with pytest.raises(ValueError):
        ContextPacket(packet.repository_root, 2000, (packet.sources[0],) * 2)
    with pytest.raises(ValueError, match="budget"):
        ContextPacket(packet.repository_root, 1, packet.sources)


@pytest.mark.parametrize(
    "kind",
    [
        "leaf_inside",
        "leaf_outside",
        "ancestor_inside",
        "ancestor_outside",
        "directory",
        "missing",
        "fifo",
    ],
)
def test_nonregular_sources_fail_before_open(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import os

    target = repository / "selected"
    source = "selected"
    if kind.startswith("leaf"):
        target.symlink_to(
            repository / "z.txt" if kind.endswith("inside") else tmp_path / "outside"
        )
    elif kind.startswith("ancestor"):
        target.symlink_to(
            repository / "docs" if kind.endswith("inside") else tmp_path,
            target_is_directory=True,
        )
        source += "/a.txt"
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("nonregular source opened")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError):
        build_context_packet(repository, source_paths=[source], budget_bytes=2000)


@pytest.mark.parametrize("operation", ["lstat", "open", "read"])
def test_filesystem_errors_are_explicit(
    repository: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import io

    original = Path.lstat

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    def inspect(path: Path) -> object:
        if path == repository / "z.txt":
            denied()
        return original(path)

    class Broken(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("read failure")

    if operation == "lstat":
        monkeypatch.setattr(Path, "lstat", inspect)
    elif operation == "open":
        monkeypatch.setattr(Path, "open", denied)
    else:
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Broken())
    with pytest.raises(ValueError, match="source"):
        build_context_packet(repository, source_paths=["z.txt"], budget_bytes=2000)


@pytest.mark.parametrize("oversized", [False, True])
def test_finite_cumulative_reads_and_short_reads(
    repository: Path, monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    import io

    budget = 1000
    payloads = {"docs/a.txt": b"a" * 30, "z.txt": b"z" * (5000 if oversized else 25)}
    returned = 0
    requests: list[int] = []
    opened: list[str] = []

    class Chunked(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            nonlocal returned
            assert size is not None
            assert 0 < size <= budget - returned + 1
            requests.append(size)
            data = super().read(min(size, 7))
            returned += len(data)
            assert returned <= budget + 1
            return data

    def selected(path: Path, mode: str) -> Chunked:
        assert mode == "rb"
        name = str(path.relative_to(repository))
        opened.append(name)
        return Chunked(payloads[name])

    monkeypatch.setattr(Path, "open", selected)
    if oversized:
        with pytest.raises(ValueError, match="budget"):
            build_context_packet(
                repository, source_paths=list(payloads), budget_bytes=budget
            )
        assert returned == budget + 1
    else:
        packet = build_context_packet(
            repository, source_paths=list(payloads), budget_bytes=budget
        )
        assert [s.text for s in packet.sources] == ["a" * 30, "z" * 25]
        assert returned == 55
    assert opened == list(payloads)
    assert len(requests) > 2


def test_serialization_overflow_stops_before_later_source(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / "docs/a.txt").write_bytes(b"\x00" * 200)
    actual_open = Path.open
    opened: list[Path] = []

    def selected(path: Path, mode: str) -> object:
        assert path == repository / "docs/a.txt"
        opened.append(path)
        return actual_open(path, mode)

    monkeypatch.setattr(Path, "open", selected)
    with pytest.raises(ValueError, match="budget"):
        build_context_packet(
            repository, source_paths=["z.txt", "docs/a.txt"], budget_bytes=1000
        )
    assert opened == [repository / "docs/a.txt"]


def test_read_only_selected_files_and_git_resolution(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {
        str(p.relative_to(repository)): p.read_bytes()
        for p in repository.rglob("*")
        if p.is_file()
    }
    actual_run = subprocess.run
    actual_open = Path.open
    calls: list[list[str]] = []

    def git_only(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == ["git", "-C", str(repository), "rev-parse", "--show-toplevel"]
        calls.append(argv)
        return actual_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    def selected(path: Path, mode: str) -> object:
        assert path == repository / "docs/a.txt"
        assert mode == "rb"
        return actual_open(path, mode)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", git_only)
        patch.setattr(Path, "open", selected)
        build_context_packet(repository, source_paths=["docs/a.txt"], budget_bytes=2000)
    assert len(calls) == 1
    assert before == {
        str(p.relative_to(repository)): p.read_bytes()
        for p in repository.rglob("*")
        if p.is_file()
    }


def test_oversized_file_not_fully_read_or_hashed(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    (repository / "docs/a.txt").write_bytes(b"a" * 100_000)

    class Observed(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size == 1001
            result = super().read(size)
            assert len(result) == 1001
            return result

    def no_hash(*args: object) -> None:
        pytest.fail("overflow payload hashed")

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Observed(b"a" * 100_000))
    monkeypatch.setattr(hashlib, "sha256", no_hash)
    with pytest.raises(ValueError, match="budget"):
        build_context_packet(
            repository, source_paths=["docs/a.txt", "z.txt"], budget_bytes=1000
        )


def test_bad_later_path_prevents_earlier_payload_read(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("payload read before all lexical validation")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError):
        build_context_packet(
            repository, source_paths=["docs/a.txt", "z/../bad"], budget_bytes=2000
        )

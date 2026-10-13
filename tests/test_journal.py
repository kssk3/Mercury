from __future__ import annotations

import json
import os
from pathlib import Path
from types import MappingProxyType

import pytest

from agent_harness.journal import EventJournal


def test_journal_reads_no_records_without_creating_a_directory(tmp_path: Path) -> None:
    journal_directory = tmp_path / "journal"

    events = EventJournal(journal_directory).read()

    assert events == []
    assert not journal_directory.exists()


def test_journal_reads_appended_records_in_append_order(tmp_path: Path) -> None:
    journal_directory = tmp_path / "journal"
    journal = EventJournal(journal_directory)

    journal.append({"event": "started"})
    journal.append({"event": "finished", "success": True})

    assert journal.read() == [
        {"event": "started"},
        {"event": "finished", "success": True},
    ]


def test_journal_appends_a_non_dict_mapping_as_a_json_object(tmp_path: Path) -> None:
    journal = EventJournal(tmp_path / "journal")
    event = MappingProxyType({"event": "finished", "success": True})

    journal.append(event)

    assert journal.read() == [{"event": "finished", "success": True}]


def test_journal_rejects_a_leaf_symlink_without_mutating_its_target(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    target = repository / "tracked.txt"
    target.write_text("original\n", encoding="utf-8")
    journal_directory = tmp_path / "journal"
    journal_directory.mkdir()
    journal = EventJournal(journal_directory)
    journal.path.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        journal.append({"event": "must-not-escape"})

    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("contents", ["not json\n", json.dumps(["not", "an object"])])
def test_journal_rejects_an_invalid_stored_record(
    tmp_path: Path,
    contents: str,
) -> None:
    journal_directory = tmp_path / "journal"
    journal_directory.mkdir()
    (journal_directory / "events.jsonl").write_text(contents)

    with pytest.raises(ValueError, match="JSON object"):
        EventJournal(journal_directory).read()


@pytest.mark.parametrize("contents", [b"", b'{"event":"existing"}\n'])
def test_journal_rejects_hardlink_without_mutating_target(
    tmp_path: Path, contents: bytes
) -> None:
    target = tmp_path / "target"
    target.write_bytes(contents)
    journal = EventJournal(tmp_path / "journal")
    journal.path.parent.mkdir()
    os.link(target, journal.path)
    with pytest.raises(ValueError, match="link"):
        journal.append({"event": "rejected"})
    assert target.read_bytes() == contents


def test_journal_checks_actual_opened_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventJournal(tmp_path / "journal")
    journal.path.parent.mkdir()
    journal.path.write_bytes(b"")
    target = tmp_path / "target"
    contents = b'{"event":"existing"}\n'
    target.write_bytes(contents)
    original = os.open

    def redirected(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == journal.path:
            journal.path.unlink()
            os.link(target, journal.path)
        return original(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", redirected)
    with pytest.raises(ValueError, match="link"):
        journal.append({"event": "rejected"})
    assert target.read_bytes() == contents


def _read_persistence(kind: str, directory: Path) -> object:
    from agent_harness.state import CurrentStateStore

    return (
        EventJournal(directory).read()
        if kind == "journal"
        else CurrentStateStore(directory).read()
    )


def _persistence_path(kind: str, directory: Path) -> Path:
    return directory / ("events.jsonl" if kind == "journal" else "current.json")


@pytest.mark.parametrize("kind", ["journal", "state"])
@pytest.mark.parametrize("hazard", ["fifo", "symlink", "hardlink"])
def test_persistence_read_rejects_unsafe_leaf(
    tmp_path: Path, kind: str, hazard: str
) -> None:
    import subprocess
    import sys

    leaf = _persistence_path(kind, tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b'{"event":"unrelated"}\n')
    if hazard == "fifo":
        os.mkfifo(leaf)
        code = (
            "from pathlib import Path\nfrom agent_harness.journal import EventJournal\n"
            "from agent_harness.state import CurrentStateStore\n"
            f"reader = {'EventJournal' if kind == 'journal' else 'CurrentStateStore'}(Path({str(tmp_path)!r}))\n"
            "print('READY', flush=True)\ntry: reader.read()\n"
            "except (ValueError, OSError): print('REJECTED', flush=True)\n"
            "else: raise AssertionError('unsafe read accepted')\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate(timeout=2)
            assert out.startswith("READY\n"), (out, err)
            pytest.fail("reader blocked after READY on real FIFO without a writer")
        assert process.returncode == 0, (out, err)
        assert out == "READY\nREJECTED\n"
    else:
        if hazard == "symlink":
            leaf.symlink_to(target)
        else:
            os.link(target, leaf)
        with pytest.raises((ValueError, OSError)):
            _read_persistence(kind, tmp_path)
    assert target.read_bytes() == b'{"event":"unrelated"}\n'


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_validates_opened_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import io
    from typing import Any

    leaf = _persistence_path(kind, tmp_path)
    leaf.write_bytes(b"{}\n")
    target = tmp_path / "target"
    target.write_bytes(b'{"event":"unrelated"}\n')
    original_open, original_io_open = os.open, io.open
    opened: list[int] = []

    def replace() -> None:
        if leaf.stat().st_nlink == 1:
            leaf.unlink()
            os.link(target, leaf)

    def actual_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == leaf:
            replace()
        fd = original_open(path, flags, *args, **kwargs)
        if path == leaf:
            opened.append(fd)
        return fd

    def actual_io_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if path == leaf:
            replace()
        return original_io_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", actual_open)
    monkeypatch.setattr(io, "open", actual_io_open)
    with pytest.raises((ValueError, OSError)):
        _read_persistence(kind, tmp_path)
    assert leaf.stat().st_nlink == 2
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_is_finite_when_file_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import io
    from typing import Any

    leaf = _persistence_path(kind, tmp_path)
    leaf.write_bytes(b"{}")
    initial_size = leaf.stat().st_size
    original_open, original_read, original_io_open = os.open, os.read, io.open
    opened: list[int] = []
    requests: list[int] = []

    def grow() -> None:
        with original_io_open(leaf, "ab") as stream:
            stream.write(b" " * 16)

    def actual_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = original_open(path, flags, *args, **kwargs)
        if path == leaf:
            opened.append(fd)
        return fd

    def actual_read(fd: int, amount: int) -> bytes:
        if fd in opened:
            requests.append(amount)
            assert 0 < amount <= initial_size + 1
            assert len(requests) <= 3, "read exceeded finite initial-size budget"
            grow()
        return original_read(fd, amount)

    class GrowingStream:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.stream.close()

        def read(self, *args: Any, **kwargs: Any) -> Any:
            grow()
            return self.stream.read(*args, **kwargs)

    def actual_io_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        stream = original_io_open(path, *args, **kwargs)
        return GrowingStream(stream) if path == leaf else stream

    monkeypatch.setattr(os, "open", actual_open)
    monkeypatch.setattr(os, "read", actual_read)
    monkeypatch.setattr(io, "open", actual_io_open)
    with pytest.raises(ValueError, match="changed"):
        _read_persistence(kind, tmp_path)
    assert leaf.stat().st_size > initial_size
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_preserves_large_existing_history(
    tmp_path: Path, kind: str
) -> None:
    payload = {"event": "existing", "text": "x" * (65536 * 3)}
    _persistence_path(kind, tmp_path).write_text(json.dumps(payload) + "\n")
    assert _read_persistence(kind, tmp_path) == (
        [payload] if kind == "journal" else payload
    )


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_closes_descriptor_on_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from typing import Any

    leaf = _persistence_path(kind, tmp_path)
    leaf.write_bytes(b"{}\n")
    original_open, original_read = os.open, os.read
    opened: list[int] = []

    def actual_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = original_open(path, flags, *args, **kwargs)
        if path == leaf:
            assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
            opened.append(fd)
        return fd

    def failed_read(fd: int, size: int) -> bytes:
        if fd in opened:
            raise OSError("injected read failure")
        return original_read(fd, size)

    monkeypatch.setattr(os, "open", actual_open)
    monkeypatch.setattr(os, "read", failed_read)
    with pytest.raises(OSError, match="injected read failure"):
        _read_persistence(kind, tmp_path)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_rejects_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from typing import Any

    leaf = _persistence_path(kind, tmp_path)
    leaf.write_bytes(b'{"existing":"history"}\n')
    original_open, original_read = os.open, os.read
    opened: list[int] = []

    def actual_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = original_open(path, flags, *args, **kwargs)
        if path == leaf:
            opened.append(fd)
        return fd

    def truncated_read(fd: int, size: int) -> bytes:
        if fd in opened:
            leaf.write_bytes(b"{}")
        return original_read(fd, size)

    monkeypatch.setattr(os, "open", actual_open)
    monkeypatch.setattr(os, "read", truncated_read)
    with pytest.raises(ValueError, match="changed"):
        _read_persistence(kind, tmp_path)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_turn_newline_reader_never_consumes_replaced_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import subprocess
    from typing import Any

    from agent_harness.turn import _validate_journal

    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=5)
    journal = EventJournal(tmp_path / "state")
    journal.append({"event": "existing"})
    target = tmp_path / "unrelated"
    contents = journal.path.read_bytes()
    target.write_bytes(contents)
    original_open, original_io_open, original_read = os.open, io.open, os.read
    opened: list[int] = []
    consumed: list[bytes] = []
    replaced = False

    def replace() -> None:
        nonlocal replaced
        if not replaced:
            journal.path.unlink()
            os.link(target, journal.path)
            replaced = True

    class ObservedStream:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.stream.close()

        def seek(self, *args: Any) -> Any:
            return self.stream.seek(*args)

        def read(self, *args: Any) -> Any:
            value = self.stream.read(*args)
            consumed.append(value)
            return value

    def actual_io_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if path == journal.path:
            replace()
        stream = original_io_open(path, *args, **kwargs)
        return ObservedStream(stream) if path == journal.path else stream

    def actual_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == journal.path:
            replace()
        fd = original_open(path, flags, *args, **kwargs)
        if path == journal.path:
            opened.append(fd)
        return fd

    def actual_read(fd: int, size: int) -> bytes:
        value = original_read(fd, size)
        if fd in opened:
            consumed.append(value)
        return value

    monkeypatch.setattr(io, "open", actual_io_open)
    monkeypatch.setattr(os, "open", actual_open)
    monkeypatch.setattr(os, "read", actual_read)
    with pytest.raises(ValueError):
        _validate_journal(journal, root)
    assert replaced
    assert sum(map(len, consumed)) == 0
    assert target.read_bytes() == contents
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("kind", ["journal", "state"])
def test_persistence_read_rejects_symlink_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from typing import Any

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    leaf = _persistence_path(kind, unrelated)
    contents = b'{"event":"unrelated","caller":"preserved"}\n'
    leaf.write_bytes(contents)
    alias = tmp_path / "state-alias"
    alias.symlink_to(unrelated, target_is_directory=True)
    selected = _persistence_path(kind, alias)
    original_open = os.open
    opened: list[object] = []

    def observe_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == selected:
            opened.append(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", observe_open)
    with pytest.raises(ValueError, match="symbolic link"):
        _read_persistence(kind, alias)
    assert opened == []
    assert leaf.read_bytes() == contents
    assert sorted(p.name for p in unrelated.iterdir()) == [leaf.name]

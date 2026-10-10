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

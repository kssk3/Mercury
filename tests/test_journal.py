from __future__ import annotations

import json
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

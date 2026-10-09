from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pytest

from agent_harness.state import CurrentStateStore


def test_store_reads_none_without_creating_state_directory(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"

    state = CurrentStateStore(state_directory).read()

    assert state is None
    assert not state_directory.exists()


def test_store_replaces_the_current_snapshot_without_a_temporary_artifact(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    store = CurrentStateStore(state_directory)

    store.write({"attempt": 1})
    store.write({"attempt": 2, "status": "running"})

    assert store.read() == {"attempt": 2, "status": "running"}
    assert sorted(path.name for path in state_directory.iterdir()) == ["current.json"]


def test_store_writes_a_non_dict_mapping_as_a_json_object(tmp_path: Path) -> None:
    store = CurrentStateStore(tmp_path / "state")
    state = MappingProxyType({"attempt": 2, "status": "running"})

    store.write(state)

    assert store.read() == {"attempt": 2, "status": "running"}


def test_store_removes_the_temporary_file_when_serialization_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    temporary_path = state_directory / ".current-failed.tmp"

    class FailingTemporaryFile:
        name = str(temporary_path)

        def __enter__(self) -> FailingTemporaryFile:
            state_directory.mkdir(exist_ok=True)
            temporary_path.touch()
            return self

        def __exit__(
            self, exc_type: object, exc: object, traceback: object
        ) -> Literal[False]:
            return False

        def write(self, value: str) -> int:
            raise OSError("disk write failed")

    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        lambda *args, **kwargs: FailingTemporaryFile(),
    )

    with pytest.raises(OSError, match="disk write failed"):
        CurrentStateStore(state_directory).write({"status": "running"})

    assert not temporary_path.exists()


def test_store_rejects_an_existing_non_object_snapshot(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / "current.json").write_text(json.dumps(["not", "an object"]))

    with pytest.raises(ValueError, match="JSON object"):
        CurrentStateStore(state_directory).read()

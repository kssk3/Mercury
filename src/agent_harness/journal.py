from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast


class EventJournal:
    def __init__(self, state_directory: Path | str) -> None:
        self._state_directory = Path(state_directory)

    @property
    def path(self) -> Path:
        return self._state_directory / "events.jsonl"

    def append(self, event: Mapping[str, object]) -> None:
        self._state_directory.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("journal path must not be a symbolic link")
        serialized = json.dumps(dict(event), separators=(",", ":"), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(f"{serialized}\n")

    def read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []

        events: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("journal records must contain JSON objects") from error
            if not isinstance(event, dict):
                raise ValueError("journal records must contain JSON objects")
            events.append(cast(dict[str, object], event))
        return events

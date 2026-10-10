from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast


class CurrentStateStore:
    def __init__(self, state_directory: Path | str) -> None:
        self._state_directory = Path(state_directory)

    @property
    def path(self) -> Path:
        return self._state_directory / "current.json"

    def read(self) -> dict[str, object] | None:
        if not self.path.exists():
            return None

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("current state must contain a JSON object") from error

        if not isinstance(payload, dict):
            raise ValueError("current state must contain a JSON object")
        return cast(dict[str, object], payload)

    def write(self, state: Mapping[str, object]) -> None:
        self._state_directory.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(dict(state), separators=(",", ":"), sort_keys=True)
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self._state_directory,
                encoding="utf-8",
                prefix=".current-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(serialized)
                temporary_file.flush()

            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

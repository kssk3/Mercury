from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredOutput:
    identifier: str
    path: Path
    byte_count: int
    truncated: bool


class OutputStore:
    def __init__(
        self,
        state_directory: Path | str,
        *,
        max_bytes: int,
        redactor: Callable[[str], str],
    ) -> None:
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be a positive integer")

        self._state_directory = Path(state_directory)
        self._max_bytes = max_bytes
        self._redactor = redactor

    def write(self, output: str) -> StoredOutput:
        redacted_output = self._redactor(output)
        stored_bytes, truncated = self._bound_utf8(redacted_output)
        identifier = hashlib.sha256(stored_bytes).hexdigest()
        output_directory = self._state_directory / "outputs"
        path = output_directory / f"{identifier}.txt"

        if output_directory.is_symlink():
            raise ValueError("outputs directory must not be a symlink")
        output_directory.mkdir(parents=True, exist_ok=True)
        self._write_atomically(path, stored_bytes)
        return StoredOutput(identifier, path, len(stored_bytes), truncated)

    def _bound_utf8(self, output: str) -> tuple[bytes, bool]:
        encoded = output.encode("utf-8")
        if len(encoded) <= self._max_bytes:
            return encoded, False
        bounded = encoded[: self._max_bytes].decode("utf-8", errors="ignore")
        return bounded.encode("utf-8"), True

    def _write_atomically(self, path: Path, contents: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=path.parent,
                prefix=".output-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(contents)
                temporary_file.flush()
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

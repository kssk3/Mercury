from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_harness.repository import validate_runtime_state_directory


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
        output_directory = self._state_directory / "outputs"
        validate_runtime_state_directory(self._state_directory)
        validate_runtime_state_directory(output_directory)
        redacted_output = self._redactor(output)
        validate_runtime_state_directory(self._state_directory)
        validate_runtime_state_directory(output_directory)
        stored_bytes, truncated = self._bound_utf8(redacted_output)
        identifier = hashlib.sha256(stored_bytes).hexdigest()
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

    def validate(self, output: StoredOutput) -> None:
        """Check one current evidence leaf without reading beyond its byte cap.

        As with persistence reads, this is a point-in-time descriptor check, not
        an atomic filesystem freeze or a promise about later operator mutations.
        """
        directory = self._state_directory / "outputs"
        validate_runtime_state_directory(self._state_directory)
        validate_runtime_state_directory(directory)
        if (
            len(output.identifier) != 64
            or any(
                character not in "0123456789abcdef" for character in output.identifier
            )
            or output.path != directory / f"{output.identifier}.txt"
            or type(output.byte_count) is not int
            or not 0 <= output.byte_count <= self._max_bytes
            or type(output.truncated) is not bool
        ):
            raise ValueError("invalid stored output metadata")
        descriptor = os.open(output.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != output.byte_count
            ):
                raise ValueError("stored output must match a regular single-link leaf")
            remaining = output.byte_count
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65536))
                if not chunk:
                    raise ValueError("stored output changed during read")
                chunks.append(chunk)
                remaining -= len(chunk)
            extra = os.read(descriptor, 1)
            after = os.fstat(descriptor)
            if extra or (
                before.st_size,
                before.st_nlink,
                before.st_mode,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_size,
                after.st_nlink,
                after.st_mode,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("stored output changed during read")
            raw = b"".join(chunks)
            raw.decode("utf-8")
            if hashlib.sha256(raw).hexdigest() != output.identifier:
                raise ValueError("stored output content does not match identifier")
        finally:
            os.close(descriptor)

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

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath


@dataclass(frozen=True)
class VerificationPolicy:
    commands: tuple[tuple[str, ...], ...]
    working_directory: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not self.commands or any(
            not isinstance(command, Sequence)
            or isinstance(command, str)
            or not command
            or any(
                not isinstance(item, str) or not item or "\x00" in item
                for item in command
            )
            for command in self.commands
        ):
            raise ValueError("at least one non-empty command is required")
        object.__setattr__(
            self, "commands", tuple(tuple(command) for command in self.commands)
        )
        if "\x00" in self.working_directory:
            raise ValueError("working directory must not contain NUL")
        path = PurePath(self.working_directory)
        if path.is_absolute():
            raise ValueError("working directory must be relative")
        if ".." in path.parts:
            raise ValueError("working directory must not contain parent traversal")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive integer")

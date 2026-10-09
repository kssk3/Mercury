from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePath


@dataclass(frozen=True)
class TaskContract:
    goal: str
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    completion_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        object.__setattr__(self, "protected_paths", tuple(self.protected_paths))
        object.__setattr__(self, "completion_criteria", tuple(self.completion_criteria))
        self._validate()

    def to_json(self) -> str:
        return json.dumps(
            {
                "allowed_paths": list(self.allowed_paths),
                "completion_criteria": list(self.completion_criteria),
                "goal": self.goal,
                "protected_paths": list(self.protected_paths),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("goal must not be empty")
        if not self.completion_criteria or any(
            not criterion.strip() for criterion in self.completion_criteria
        ):
            raise ValueError("at least one non-empty criterion is required")

        self._validate_paths(self.allowed_paths)
        self._validate_paths(self.protected_paths)
        allowed_keys = tuple(path.casefold() for path in self.allowed_paths)
        protected_keys = tuple(path.casefold() for path in self.protected_paths)
        if any(
            protected == allowed
            or protected.startswith(f"{allowed}/")
            or allowed.startswith(f"{protected}/")
            for allowed in allowed_keys
            for protected in protected_keys
        ):
            raise ValueError("a path cannot be both allowed and protected")

    @staticmethod
    def _validate_paths(paths: tuple[str, ...]) -> None:
        if len(paths) != len({path.casefold() for path in paths}):
            raise ValueError("duplicate paths are not allowed")
        for path in paths:
            if "\x00" in path:
                raise ValueError("paths must not contain NUL")
            pure_path = PurePath(path)
            if pure_path.is_absolute():
                raise ValueError("paths must be relative")
            if ".." in pure_path.parts:
                raise ValueError("paths must not contain parent traversal")
            if str(pure_path) != path or path in {"", "."}:
                raise ValueError("paths must be canonical")

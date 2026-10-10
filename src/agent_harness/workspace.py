from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_harness.repository import git_read_environment


@dataclass(frozen=True)
class WorkspaceSnapshot:
    staged_paths: tuple[str, ...]
    unstaged_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]

    @classmethod
    def capture(cls, repository: Path | str) -> WorkspaceSnapshot:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(Path(repository).resolve()),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            capture_output=True,
            check=False,
            env=git_read_environment(),
        )
        if result.returncode != 0:
            raise ValueError("repository must be a Git working tree")
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        records = iter((result.stdout or b"").split(b"\0"))
        for raw_record in records:
            if not raw_record:
                continue
            record = os.fsdecode(raw_record)
            status, path = record[:2], record[3:]
            if status == "??":
                untracked.append(path)
                continue
            if status[0] != " ":
                staged.append(path)
            if status[1] != " ":
                unstaged.append(path)
            if "R" not in status and "C" not in status:
                continue
            source = os.fsdecode(next(records))
            if status[0] == "R":
                staged.append(source)
            if status[1] == "R":
                unstaged.append(source)
        return cls(tuple(staged), tuple(unstaged), tuple(untracked))

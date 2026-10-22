"""Read-only filename evidence, with bounded POSIX Git inventory capture.

Inventory gets 10 seconds through process exit and pipe EOF, 8 MiB stdout and
64 KiB stderr. Failure cleanup controls its process group with a 1-second reap
budget; it is not a sandbox or a deadline for the subsequent filesystem walk.
"""

from __future__ import annotations

import os
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent_harness.repository import (
    git_executable,
    git_read_environment,
    resolve_repository_root,
)

_LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
}
_MANIFEST_NAMES = frozenset(
    {"pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod"}
)
_DOCUMENT_NAMES = frozenset({"README.md", "AGENTS.md"})
_INVENTORY_TIMEOUT_SECONDS = 10.0
_INVENTORY_STDOUT_LIMIT = 8388608
_INVENTORY_STDERR_LIMIT = 65536


@dataclass(frozen=True)
class RepositoryProfile:
    repository_root: str
    file_count: int
    languages: tuple[str, ...]
    manifests: tuple[str, ...]
    documents: tuple[str, ...]
    top_level_directories: tuple[str, ...]


def _is_regular_without_symlinks(root: Path, relative: Path) -> bool:
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return False
        if stat.S_ISLNK(mode):
            return False
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            return False
    return stat.S_ISREG(mode)


def _capture_inventory(root: Path) -> bytes:
    """Capture both byte streams without parsing partial or over-budget output.

    The same deadline covers an exited leader whose descendants retain pipes.
    Failures discard all output and kill the owned POSIX process group while its
    leader remains unreaped; detached descendants are outside this boundary.
    """
    message = "could not inspect Git file inventory"
    deadline = time.monotonic() + _INVENTORY_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            [
                git_executable(),
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_read_environment(),
            start_new_session=True,
        )
    except OSError as error:
        raise ValueError(message) from error

    output = bytearray()
    counts = {"stdout": 0, "stderr": 0}
    limits = {"stdout": _INVENTORY_STDOUT_LIMIT, "stderr": _INVENTORY_STDERR_LIMIT}
    selector: selectors.BaseSelector | None = None
    failure: BaseException | None = None
    try:
        selector = selectors.DefaultSelector()
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            assert pipe is not None
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                message += ": timeout"
                raise ValueError(message)
            for key, _ in selector.select(remaining):
                name = key.data
                # Read at most the remaining allowance plus the first excess byte.
                chunk = os.read(key.fd, min(65536, limits[name] - counts[name] + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                counts[name] += len(chunk)
                if counts[name] > limits[name]:
                    message += f": {name} limit exceeded"
                    raise ValueError(message)
                if name == "stdout":
                    output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            message += ": timeout"
            raise ValueError(message)
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            message += ": timeout"
            raise
        if returncode != 0:
            raise ValueError(message)
    except BaseException as error:
        failure = error

    cleanup_errors: list[BaseException] = []
    cleanup_deadline = time.monotonic() + 1.0
    # Cached returncode also reflects reaping performed inside an interrupted wait.
    # Do not poll: an exited but unreaped leader still reserves the owned PGID.
    if failure is not None and process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        except BaseException as error:
            cleanup_errors.append(error)
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except BaseException as error:
                cleanup_errors.append(error)
    if selector is not None:
        try:
            selector.close()
        except BaseException as error:
            cleanup_errors.append(error)
    for pending_error in (failure, *cleanup_errors):
        if pending_error is not None and not isinstance(pending_error, Exception):
            raise pending_error
    if cleanup_errors:
        message += ": cleanup failed"
    if failure is not None or cleanup_errors:
        raise ValueError(message) from failure
    return bytes(output)


def build_repository_profile(path: Path | str) -> RepositoryProfile:
    try:
        root = resolve_repository_root(path)
    except subprocess.TimeoutExpired as error:
        raise ValueError("could not inspect Git working tree: timeout") from error
    inventory = _capture_inventory(root)

    paths = {
        Path(os.fsdecode(raw_path)) for raw_path in inventory.split(b"\0") if raw_path
    }
    try:
        included = sorted(
            (
                relative
                for relative in paths
                if _is_regular_without_symlinks(root, relative)
            ),
            key=lambda relative: relative.as_posix(),
        )
    except OSError as error:
        raise ValueError("could not inspect working-tree files") from error
    languages = {
        _LANGUAGE_SUFFIXES[path.suffix]
        for path in included
        if path.suffix in _LANGUAGE_SUFFIXES
    }
    manifests = {path.as_posix() for path in included if path.name in _MANIFEST_NAMES}
    documents = {path.as_posix() for path in included if path.name in _DOCUMENT_NAMES}
    top_level = {path.parts[0] for path in included if len(path.parts) > 1}
    return RepositoryProfile(
        repository_root=str(root),
        file_count=len(included),
        languages=tuple(sorted(languages)),
        manifests=tuple(sorted(manifests)),
        documents=tuple(sorted(documents)),
        top_level_directories=tuple(sorted(top_level)),
    )

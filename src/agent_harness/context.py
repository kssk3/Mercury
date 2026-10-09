"""Explicit, bounded repository context; content is untrusted data."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath

from agent_harness.repository import resolve_repository_root


def _relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("source path must be a nonempty NUL-free string")
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise ValueError("source path must be canonical and relative")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("source path must be UTF-8") from error


def _budget(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("budget_bytes must be a positive integer")


@dataclass(frozen=True)
class ContextSource:
    """Complete strict UTF-8 source bytes, identified by path and SHA-256."""

    source_path: str
    source_sha256: str
    size_bytes: int
    text: str

    def __post_init__(self) -> None:
        _relative_path(self.source_path)
        if not isinstance(self.text, str):
            raise ValueError("source text must be UTF-8 text")
        try:
            raw = self.text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("source text must be UTF-8") from error
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes != len(raw)
        ):
            raise ValueError("source byte count does not match text")
        if self.source_sha256 != hashlib.sha256(raw).hexdigest():
            raise ValueError("source SHA-256 does not match text")


@dataclass(frozen=True)
class ContextPacket:
    """Immutable sources whose complete canonical JSON fits the declared budget."""

    repository_root: str
    budget_bytes: int
    sources: tuple[ContextSource, ...]

    def __post_init__(self) -> None:
        root = self.repository_root
        if (
            not isinstance(root, str)
            or "\x00" in root
            or root.startswith("//")
            or not Path(root).is_absolute()
            or str(Path(root)) != root
            or ".." in Path(root).parts
        ):
            raise ValueError("repository root must be canonical and absolute")
        try:
            root.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("repository root must be UTF-8") from error
        _budget(self.budget_bytes)
        if (
            not isinstance(self.sources, Sequence)
            or isinstance(self.sources, str)
            or any(not isinstance(source, ContextSource) for source in self.sources)
        ):
            raise ValueError("sources must be a sequence of ContextSource records")
        sources = tuple(sorted(self.sources, key=lambda source: source.source_path))
        if len({source.source_path for source in sources}) != len(sources):
            raise ValueError("duplicate source paths")
        object.__setattr__(self, "sources", sources)
        if self.size_bytes > self.budget_bytes:
            raise ValueError("context packet exceeds budget")

    def to_json(self) -> str:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @property
    def size_bytes(self) -> int:
        return len(self.to_json().encode("utf-8"))


def _selected_file(root: Path, relative: str) -> Path:
    current = root
    parts = Path(relative).parts
    try:
        for index, component in enumerate(parts):
            current /= component
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("selected source must not contain symlinks")
            if index < len(parts) - 1:
                if not stat.S_ISDIR(mode):
                    raise ValueError("selected source ancestor must be a directory")
            elif not stat.S_ISREG(mode):
                raise ValueError("selected source must be a regular file")
    except OSError as error:
        raise ValueError("could not inspect selected source") from error
    return current


def _read_source(path: Path, allowance: int) -> bytes:
    payload = bytearray()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(min(65536, allowance - len(payload) + 1))
                payload.extend(chunk)
                if len(payload) > allowance:
                    raise ValueError("source payload exceeds budget")
                if not chunk:
                    return bytes(payload)
    except OSError as error:
        raise ValueError("could not read selected source") from error


def build_context_packet(
    repository_path: Path | str, *, source_paths: Sequence[str], budget_bytes: int
) -> ContextPacket:
    """Capture explicit regular files without executing content or writing state.

    Reject symlinks and over-budget captures rather than truncating. Filesystem
    checks are point-in-time best effort, not an atomic concurrent snapshot.
    The budget bounds returned payload and JSON size, not process memory.
    """
    _budget(budget_bytes)
    if not isinstance(source_paths, Sequence) or isinstance(source_paths, str):
        raise ValueError("source_paths must be a finite sequence of relative strings")
    paths = tuple(source_paths)
    for path in paths:
        _relative_path(path)
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate source paths")
    try:
        root = resolve_repository_root(repository_path)
    except OSError as error:
        raise ValueError("could not inspect repository for selected sources") from error
    packet = ContextPacket(str(root), budget_bytes, ())
    serialized_size = packet.size_bytes
    sources: list[ContextSource] = []
    total = 0
    for path in sorted(paths):
        raw = _read_source(_selected_file(root, path), budget_bytes - total)
        total += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("source must be UTF-8") from error
        source = ContextSource(path, hashlib.sha256(raw).hexdigest(), len(raw), text)
        # The empty packet already includes []; each source adds its exact JSON
        # bytes and, after the first source, one separating comma.
        serialized_size += len(
            json.dumps(
                asdict(source),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        serialized_size += bool(sources)
        if serialized_size > budget_bytes:
            raise ValueError("context packet exceeds budget")
        sources.append(source)
    return ContextPacket(str(root), budget_bytes, tuple(sources)) if sources else packet

"""Standard-library-only, explicit pre-import source identity gate.

The selected interpreter, this bootstrap and externally pinned manifest digest
are trusted inputs. This is a point-in-time check, not hostile-host confinement.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

_MANIFEST_BYTES = 2 * 1024 * 1024
_FILE_BYTES = 16 * 1024 * 1024
_TOTAL_BYTES = 256 * 1024 * 1024
_FILE_COUNT = 10000
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_ERROR = "verified runtime integrity check failed"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR)


def _canonical(value: object) -> Path:
    if type(value) is not str or not value:
        raise ValueError(_ERROR)
    path = Path(value)
    _require(path.is_absolute() and str(path) == value and path.resolve() == path)
    # resolve equality alone can hide a self-referential/identity symlink.
    _require(not any(part.is_symlink() for part in (path, *path.parents)))
    return path


def _read(path: Path, limit: int) -> bytes:
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    _require(info.st_size <= limit)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _require(len(data) <= limit)
    return data


def _hash(path: Path, limit: int) -> str:
    return hashlib.sha256(_read(path, limit)).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _identity(value: object, actual: Path) -> None:
    if type(value) is not dict:
        raise ValueError(_ERROR)
    _require(set(value) == {"path", "sha256"})
    _require(_canonical(value["path"]) == actual)
    _require(type(value["sha256"]) is str and bool(_DIGEST.fullmatch(value["sha256"])))
    _require(_hash(actual, _TOTAL_BYTES) == value["sha256"])


def verify(root_value: str, manifest_value: str, expected: str) -> Path:
    """Return canonical source only after the complete frozen inventory matches."""
    _require(bool(_DIGEST.fullmatch(expected)))
    root = _canonical(root_value)
    _require(root.is_dir())
    manifest_path = _canonical(manifest_value)
    raw = _read(manifest_path, _MANIFEST_BYTES)
    _require(hashlib.sha256(raw).hexdigest() == expected)
    data = json.loads(raw, object_pairs_hook=_unique)
    _require(type(data) is dict)
    _require(
        set(data)
        == {
            "schema_version",
            "source_revision",
            "runtime_root",
            "python",
            "bootstrap",
            "files",
        }
    )
    _require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    _require(
        type(data["source_revision"]) is str
        and bool(_REVISION.fullmatch(data["source_revision"]))
    )
    _require(_canonical(data["runtime_root"]) == root)
    _identity(data["python"], Path(sys.executable).resolve())
    _identity(data["bootstrap"], Path(__file__).resolve())
    files = data["files"]
    _require(type(files) is dict and 0 < len(files) <= _FILE_COUNT)
    for relative, digest in files.items():
        _require(type(relative) is str and 0 < len(relative) <= 4096)
        path = Path(relative)
        _require(not path.is_absolute() and path.as_posix() == relative)
        _require(all(part not in (".", "..") for part in relative.split("/")))
        _require("\\" not in relative and "\x00" not in relative)
        _require(type(digest) is str and bool(_DIGEST.fullmatch(digest)))
    source = root / "src"
    _require(source.is_dir() and not source.is_symlink())
    actual: dict[str, str] = {}
    total = 0
    metadata_count = 0
    # Traverse explicitly: never recurse through links or silently skip special files.
    pending = [source]
    entries = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as scan:
            for entry in scan:
                entries += 1
                _require(entries <= _FILE_COUNT * 2)
                info = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
                relative = path.relative_to(source).as_posix()
                _require(relative in files)
                # Even an admitted manifest must not enable unchecked bytecode/native code.
                metadata = (
                    len(path.relative_to(source).parts) == 2
                    and path.parent.name.startswith("agent_harness-")
                    and path.parent.name.endswith(".dist-info")
                    and path.name == "METADATA"
                )
                metadata_count += int(metadata)
                _require(metadata_count <= 1)
                python_source = path.suffix == ".py" and not any(
                    part.endswith(".dist-info")
                    for part in path.relative_to(source).parts
                )
                _require(python_source or metadata)
                content = _read(path, _FILE_BYTES)
                total += len(content)
                _require(total <= _TOTAL_BYTES)
                actual[relative] = hashlib.sha256(content).hexdigest()
    _require(actual == files)
    _require("agent_harness/__init__.py" in files and "agent_harness/cli.py" in files)
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        _require(
            bool(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
        )
        source = verify(args.root, args.manifest, args.manifest_sha256)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        OverflowError,
        RuntimeError,
    ):
        print(_ERROR, file=sys.stderr)
        return 78
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    sys.path.insert(0, str(source))
    sys.argv = ["local-harness", *arguments]
    cli = importlib.import_module("agent_harness.cli")
    result: int = cli.main()
    return result


if __name__ == "__main__":
    raise SystemExit(main())

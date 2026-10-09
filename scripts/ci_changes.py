"""Fail-closed classification of committed CI change paths (standard library)."""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


class EvidenceError(ValueError):
    """Git evidence is unavailable or cannot establish safe classification."""


def git_bytes(*arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("Git evidence unavailable") from exc
    if result.returncode != 0:
        raise EvidenceError("Git evidence unavailable or invalid")
    return result.stdout


def resolve_commit(revision: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise EvidenceError("Require exact lowercase full base/head SHAs")
    if git_bytes("cat-file", "-t", revision).strip() != b"commit":
        raise EvidenceError("Revision must identify an available commit")
    resolved = (
        git_bytes("rev-parse", "--verify", revision + "^{commit}")
        .decode("ascii")
        .strip()
    )
    if resolved != revision:
        raise EvidenceError("Resolved commit differs from supplied revision")
    return resolved


def is_record_path(path: str) -> bool:
    # Compare raw components: normalizing would hide traversal or empty segments.
    parts = path.split("/")
    if len(parts) != 3 or parts[:2] != ["docs", "work-units"]:
        return False
    name = parts[2]
    if name.startswith(".") or "\\" in name:
        return False
    if any(
        (ord(char) < 32 and char != "\t")
        or ord(char) == 127
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in name
    ):
        return False
    return any(
        name.endswith(suffix) and len(name) > len(suffix)
        for suffix in ("-report.md", "-summary.md")
    )


def is_safe_path(path: str) -> bool:
    parts = path.split("/")
    if "\\" in path or any(unicodedata.category(char).startswith("C") for char in path):
        return False
    checked = (
        parts[1:] if parts[0] in {".agents", ".github", ".project-state"} else parts
    )
    return all(part and not part.startswith(".") for part in checked)


def is_document_path(path: str) -> bool:
    if not is_safe_path(path):
        return False
    parts = path.split("/")
    return (
        path in {"README.md", "AGENTS.md", ".github/pull_request_template.md"}
        or (len(parts) >= 2 and parts[0] == "docs" and path.endswith(".md"))
        or (
            len(parts) >= 4
            and parts[:2] == [".agents", "skills"]
            and parts[-1] == "SKILL.md"
        )
    )


def regular_changed_files(base: str, head: str, paths: list[str]) -> bool:
    # Check both endpoint trees: deleting/replacing a special entry is not exempt.
    seen: set[str] = set()
    regular = True
    for revision in (base, head):
        raw = git_bytes("ls-tree", "-r", "-z", revision, "--", *paths)
        if raw and not raw.endswith(b"\0"):
            raise EvidenceError("Incomplete NUL-delimited Git tree evidence")
        for entry in raw.split(b"\0")[:-1]:
            metadata, separator, name = entry.partition(b"\t")
            fields = metadata.split(b" ")
            if (
                not separator
                or len(fields) != 3
                or re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None
            ):
                raise EvidenceError("Invalid Git tree evidence")
            path = os.fsdecode(name)
            if path not in paths:
                raise EvidenceError("Unexpected Git tree path evidence")
            seen.add(path)
            if fields[:2] != [b"100644", b"blob"]:
                regular = False
    if seen != set(paths):
        raise EvidenceError("Missing changed-path tree evidence")
    return regular


def classify(base: str, head: str) -> dict[str, object]:
    resolved_base = resolve_commit(base)
    resolved_head = resolve_commit(head)
    raw = git_bytes(
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--name-only",
        "-z",
        resolved_base,
        resolved_head,
        "--",
    )
    if raw and not raw.endswith(b"\0"):
        raise EvidenceError("Incomplete NUL-delimited Git path evidence")
    paths = [os.fsdecode(path) for path in raw.split(b"\0")[:-1]] if raw else []
    state_path = ".project-state/current.json"
    record_only = (
        bool(paths)
        and all(is_document_path(path) or path == state_path for path in paths)
        and regular_changed_files(resolved_base, resolved_head, paths)
    )
    verification = "state" if state_path in paths else "document"
    if not record_only:
        verification = "full"
    return {
        "base": resolved_base,
        "head": resolved_head,
        "changed_paths": paths,
        "record_only": record_only,
        "macos_required": not record_only,
        "verification": verification,
        "state_required": verification != "document",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        result = classify(args.base, args.head)
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as stream:
                stream.write(f"record_only={str(result['record_only']).lower()}\n")
                stream.write(
                    f"macos_required={str(result['macos_required']).lower()}\n"
                )
                stream.write(f"verification={result['verification']}\n")
                stream.write(
                    f"state_required={str(result['state_required']).lower()}\n"
                )
    except (EvidenceError, OSError, UnicodeError) as exc:
        print(f"CI classification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

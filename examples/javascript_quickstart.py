"""Explicitly approved, retained JavaScript demo through the actual run CLI.

One Claude invocation, no retry, USD 0.25 native CLI cap. Authentication/model,
settings and hooks are inherited; this is not hostile-process containment.
No package installation or network bootstrap. Run artifacts remain on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path

from agent_harness.cli import main as run_cli
from agent_harness.repository import (
    git_executable,
    git_read_environment,
    validate_runtime_state_directory,
)

BUG = "export function add(a, b) { return a - b; }\n"
CHECK = (
    "import assert from 'node:assert/strict';\n"
    "import { add } from './add.mjs';\n"
    "assert.equal(add(2, 3), 5);\n"
    "assert.equal(add(-2, -3), -5);\n"
    "assert.equal(add(0, 0), 0);\n"
    "assert.equal(add(-2, 3), 1);\n"
)


def _executable(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        raise ValueError
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError
    return resolved


def _git(root: Path, *arguments: str) -> bytes:
    environment = git_read_environment()
    environment.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
    )
    result = subprocess.run(
        [
            git_executable(),
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={root.parent / 'empty-hooks'}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Mercury Example",
            "-c",
            "user.email=example@example.invalid",
            "-C",
            str(root),
            *arguments,
        ],
        env=environment,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return result.stdout


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-demo", action="store_true")
    parser.add_argument(
        "--node", required=True, help="absolute trusted Node executable"
    )
    parser.add_argument(
        "--executable", required=True, help="absolute trusted Claude executable"
    )
    parser.add_argument(
        "--evidence-dir", required=True, help="new external directory, retained"
    )
    arguments = parser.parse_args(argv)
    if not arguments.approve_demo:
        print("javascript-example: approval_required", file=sys.stderr)
        return 2
    try:
        evidence = Path(arguments.evidence_dir)
        if not evidence.is_absolute() or str(evidence) != arguments.evidence_dir:
            raise ValueError
        validate_runtime_state_directory(evidence)
        if evidence.resolve() != evidence or evidence.exists():
            raise ValueError
        mercury = Path(__file__).resolve().parents[1]
        if evidence.is_relative_to(mercury) or mercury.is_relative_to(evidence):
            raise ValueError
        node = _executable(arguments.node)
        native = _executable(arguments.executable)
        if node.is_relative_to(evidence) or native.is_relative_to(evidence):
            raise ValueError
    except (OSError, ValueError):
        print("javascript-example: invalid_demo_inputs", file=sys.stderr)
        return 2
    try:
        # Never overwrite existing evidence, including an empty directory.
        evidence.mkdir(parents=True, exist_ok=False)
        root = evidence / "repository"
        root.mkdir()
        (evidence / "empty-hooks").mkdir()
        (root / "add.mjs").write_text(BUG, encoding="utf-8")
        (root / "check.mjs").write_text(CHECK, encoding="utf-8")
        _git(root, "init", "--quiet", "--template=", "--initial-branch=demo")
        _git(root, "add", "add.mjs", "check.mjs")
        _git(root, "commit", "--quiet", "-m", "Create disposable JavaScript fixture")
        head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
        command = [str(node), "check.mjs"]
        criterion = "addition handles positive, negative and zero inputs"
        config = {
            "schema_version": 1,
            "repository": str(root),
            "expected_head": head,
            "state_root": str(evidence / "state"),
            "backend": "claude",
            "executable": str(native),
            "contract": {
                "goal": "Fix add.mjs to add two numbers; preserve check.mjs",
                "allowed_paths": ["add.mjs"],
                "protected_paths": ["check.mjs"],
                "completion_criteria": [criterion],
            },
            "verification": {
                "commands": [command],
                "working_directory": ".",
                "timeout_seconds": 5,
            },
            "criterion_commands": {criterion: [command]},
            "context_paths": ["add.mjs", "check.mjs"],
            "budgets": {
                "timeout_seconds": 90,
                "input_bytes": 16384,
                "stdout_bytes": 65536,
                "stderr_bytes": 16384,
                "final_bytes": 8192,
            },
            "total_timeout_seconds": 120,
        }
        raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        config_path = evidence / "run.json"
        config_path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_cli(
                [
                    "run",
                    "--config",
                    str(config_path),
                    "--approve-config-sha256",
                    digest,
                    "--approver",
                    "approved-javascript-demo",
                ]
            )
        outcome = (
            json.loads(buffer.getvalue())
            if buffer.getvalue().strip()
            else {"status": "blocked", "reason": "run_cli_rejected"}
        )
        print(
            json.dumps(
                {"evidence_dir": str(evidence), "result": outcome},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return code
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "evidence_dir": str(evidence),
                    "result": {
                        "status": "unknown",
                        "reason": "demo_preparation_failed",
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

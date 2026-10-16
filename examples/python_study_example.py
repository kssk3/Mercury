"""One approved addition-CLI task in a retained copy of pinned python_study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from codex_quickstart import RecordingAdapter, configuration, phase_facts

from agent_harness.adapter import TurnBudgets
from agent_harness.admission import record_admission
from agent_harness.context import build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.inspection import summarize_execution
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits
from agent_harness.state import CurrentStateStore

SOURCE_REVISION = "f990af301da73f66c0482911a6cefca2234ce09c"
TARGET = "chapter09/03.py"
CHECK_PATH = "p2_checks/check_cli_sum.py"
PROTECTED = (
    CHECK_PATH,
    ".gitignore",
    ".python-version",
    ".pre-commit-config.yaml",
    "pyproject.toml",
    "uv.lock",
)
CRITERION = "Two integers sum correctly; invalid argument count/type exits2 with stderr and no traceback."
CHECKER = '''"""Eight protected black-box CLI cases; no project imports or dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
normal = [(('2', '3'), '5\\n'), (('-3', '1'), '-2\\n'), (('0', '0'), '0\\n')]
invalid = [(), ('2',), ('2', '3', '4'), ('word', '3'), ('3', 'word')]
try:
    for args, expected in normal:
        result = subprocess.run([sys.executable, '-I', '-B', str(root/'chapter09/03.py'), *args], cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=1)
        if result.returncode != 0 or result.stdout != expected or result.stderr:
            print(json.dumps({'status':'SETUP_OR_NORMAL_FAILURE'}))
            sys.exit(3)
    failed = 0
    for args in invalid:
        result = subprocess.run([sys.executable, '-I', '-B', str(root/'chapter09/03.py'), *args], cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=1)
        failed += int(not (result.returncode == 2 and not result.stdout and result.stderr.strip() and 'Traceback' not in result.stderr))
    print(json.dumps({'normal_passed':3, 'invalid_passed':5-failed, 'cases':8}))
    sys.exit(int(bool(failed)))
except (OSError, subprocess.TimeoutExpired, UnicodeError):
    print(json.dumps({'status':'ENVIRONMENT_OR_TIMEOUT'}))
    sys.exit(3)
'''


def git_bytes(root: Path, *args: str) -> bytes:
    """Bounded local Git without replacements, hooks, signing, or global config."""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
    )
    return subprocess.run(
        (
            "/usr/bin/git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=P2 Preparation",
            "-c",
            "user.email=p2@example.invalid",
            "-C",
            str(root),
            *args,
        ),
        env=env,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout


def git(root: Path, *args: str) -> str:
    return git_bytes(root, *args).decode("utf-8").strip()


def require_external(root: Path, evidence: Path) -> None:
    if (
        evidence == root
        or evidence.is_relative_to(root)
        or root.is_relative_to(evidence)
    ):
        raise ValueError("evidence must be separate from the working copy")


def prepare(
    source: Path, root: Path, evidence: Path, check_python: Path
) -> dict[str, object]:
    """Copy committed content only; source dirty work is never read into the copy."""
    source = source.resolve(strict=True)
    root, evidence = root.resolve(), evidence.resolve()
    if root.is_relative_to(source) or evidence.is_relative_to(source):
        raise ValueError("copy and evidence must stay outside the source")
    require_external(root, evidence)
    if root.exists() or evidence.exists():
        raise ValueError("owned copy and evidence must be fresh")
    if not check_python.is_absolute() or not check_python.is_file():
        raise ValueError("trusted absolute check Python required")
    if (
        git(source, "rev-parse", "--show-toplevel") != str(source)
        or git(source, "rev-parse", "HEAD") != SOURCE_REVISION
    ):
        raise ValueError("source Git root/revision differs from the admitted source")
    entries: list[tuple[str, str, Path]] = []
    rows = git_bytes(source, "ls-tree", "-rz", "--full-tree", SOURCE_REVISION)
    for row in rows.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = Path(raw_path.decode("utf-8"))
        if (
            mode not in ("100644", "100755")
            or kind != "blob"
            or path.is_absolute()
            or any(
                part.casefold() in ("..", ".git", ".codex", ".agents")
                for part in path.parts
            )
            or path.name in ("AGENTS.md", "CODEX.md")
        ):
            raise ValueError("unsupported copied native settings or nonregular content")
        entries.append((mode, oid, path))
    root.mkdir(mode=0o700, parents=True)
    git(root, "init", "--template=", "--initial-branch=p2-owned")
    for mode, oid, path in entries:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(git_bytes(source, "cat-file", "blob", oid))
        destination.chmod(0o755 if mode == "100755" else 0o644)
    git(root, "add", "--force", "--all")
    source_tree = git(root, "write-tree")
    if source_tree != git(source, "rev-parse", SOURCE_REVISION + "^{tree}"):
        raise ValueError("copied source tree mismatch")
    git(root, "commit", "-m", "Create owned root from admitted source tree")
    checker = root / CHECK_PATH
    checker.parent.mkdir()
    checker.write_text(CHECKER, encoding="utf-8")
    git(root, "add", "--", CHECK_PATH)
    git(root, "commit", "-m", "Add protected eight-case P2 preparation checker")
    if git(root, "status", "--porcelain") or git(root, "remote"):
        raise ValueError("prepared copy must be clean and have no remotes")
    evidence.mkdir(mode=0o700, parents=True)
    manifest: dict[str, object] = {
        "source_head": SOURCE_REVISION,
        "source_tree": source_tree,
        "prepared_head": git(root, "rev-parse", "HEAD"),
        "root": str(root),
        "check_python": str(check_python),
        "checker_sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
        "prepared_snapshot": snapshot_payload(independent_snapshot(root)),
    }
    (evidence / "preparation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def independent_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Bounded, ignore-independent scan; never follow symlinks or special files."""
    result: dict[str, tuple[int, str]] = {}
    deadline = time.monotonic() + 5
    remaining = 128 * 1024 * 1024

    def walk(directory: int, prefix: str) -> None:
        nonlocal remaining
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(result) >= 10000 or time.monotonic() > deadline:
                    raise ValueError("independent scan limit")
                relative = prefix + entry.name
                before = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                mode = before.st_mode
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if stat.S_ISDIR(mode):
                    descriptor = os.open(
                        entry.name, flags | os.O_DIRECTORY, dir_fd=directory
                    )
                    try:
                        opened = os.fstat(descriptor)
                        if (opened.st_dev, opened.st_ino) != (
                            before.st_dev,
                            before.st_ino,
                        ):
                            raise ValueError("independent scan changed directory")
                        result[relative] = (mode, "directory")
                        walk(descriptor, relative + "/")
                    finally:
                        os.close(descriptor)
                elif stat.S_ISREG(mode) and before.st_nlink == 1:
                    if before.st_size > min(16 * 1024 * 1024, remaining):
                        raise ValueError("independent scan byte limit")
                    descriptor = os.open(entry.name, flags, dir_fd=directory)
                    try:
                        opened = os.fstat(descriptor)
                        if opened != before:
                            raise ValueError("independent scan changed file")
                        digest = hashlib.sha256()
                        count = 0
                        while chunk := os.read(descriptor, 65536):
                            count += len(chunk)
                            remaining -= len(chunk)
                            if (
                                count > before.st_size
                                or remaining < 0
                                or time.monotonic() > deadline
                            ):
                                raise ValueError("independent scan byte/time limit")
                            digest.update(chunk)
                        after = os.fstat(descriptor)

                        # Reads may change atime; identity, content size and modification facts must stay fixed.
                        def signature(info: os.stat_result) -> tuple[int, ...]:
                            return (
                                info.st_dev,
                                info.st_ino,
                                info.st_mode,
                                info.st_nlink,
                                info.st_size,
                                info.st_mtime_ns,
                                info.st_ctime_ns,
                            )

                        if count != before.st_size or signature(after) != signature(
                            before
                        ):
                            raise ValueError("independent scan unstable file")
                        result[relative] = (mode, digest.hexdigest())
                    finally:
                        os.close(descriptor)
                else:
                    raise ValueError("independent scan unsupported entry")
                rebound = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                if (rebound.st_dev, rebound.st_ino, rebound.st_mode) != (
                    before.st_dev,
                    before.st_ino,
                    mode,
                ):
                    raise ValueError("independent scan changed binding")

    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(descriptor, "")
    finally:
        os.close(descriptor)
    return result


def snapshot_payload(snapshot: dict[str, tuple[int, str]]) -> dict[str, object]:
    """JSON-stable preparation provenance for the bounded scanner's exact facts."""
    return {
        "schema_version": 1,
        "entries": {path: list(facts) for path, facts in snapshot.items()},
    }


class GuardedJournal(EventJournal):
    """Refuse terminal PASS persistence when independent observations disagree."""

    def __init__(
        self, state: Path, root: Path, prepared: dict[str, tuple[int, str]]
    ) -> None:
        super().__init__(state)
        self.root = root
        self.before = prepared
        self.guard_failure = False

    def check(self) -> None:
        try:
            after = independent_snapshot(self.root)
            self.guard_failure = (
                {key: value for key, value in self.before.items() if key != TARGET}
                != {key: value for key, value in after.items() if key != TARGET}
                or TARGET not in after
                or after[TARGET][0] != self.before[TARGET][0]
            )
        except (OSError, ValueError):
            self.guard_failure = True

    def append(self, event: Mapping[str, object]) -> None:
        if event.get("event") == "loop_outcome":
            self.check()
            if self.guard_failure and event.get("status") == "pass":
                # No terminal PASS is written; the read-only consumer returns UNKNOWN.
                raise ValueError("independent example guard refused terminal success")
        super().append(event)


def execute(
    root: Path,
    evidence: Path,
    check_python: Path,
    executable: str,
    selected: dict[str, object],
) -> dict[str, object]:
    """One admitted native attempt, three distinct checks, retained external state."""
    root, evidence = root.resolve(strict=True), evidence.resolve(strict=True)
    require_external(root, evidence)
    manifest = json.loads((evidence / "preparation.json").read_text())
    prepared = independent_snapshot(root)
    if manifest.get("prepared_snapshot") != snapshot_payload(prepared):
        raise ValueError("prepared copy differs from the admitted preparation baseline")
    if (
        manifest.get("root") != str(root)
        or manifest.get("source_head") != SOURCE_REVISION
        or manifest.get("check_python") != str(check_python)
        or manifest.get("prepared_head") != git(root, "rev-parse", "HEAD")
        or manifest.get("checker_sha256")
        != hashlib.sha256((root / CHECK_PATH).read_bytes()).hexdigest()
        or manifest.get("checker_sha256")
        != hashlib.sha256(CHECKER.encode("utf-8")).hexdigest()
        or git(root, "status", "--porcelain")
        or git(root, "remote")
    ):
        raise ValueError("prepared copy differs from the admitted clean baseline")
    if (evidence / "summary.json").exists() or (evidence / "state").exists():
        raise ValueError("execution evidence already exists; retry/resume refused")
    contract = TaskContract(
        goal="In this owned copy only, fix chapter09/03.py. Accept exactly two integer arguments and print their sum plus newline, exit0 and no stderr. For invalid count or noninteger print guidance only to stderr, exit2, no traceback. Preserve negative and zero integers. Do not edit other files, checks, configuration or Git metadata; no commits, extra agents, network, services or installs.",
        allowed_paths=(TARGET,),
        protected_paths=PROTECTED,
        completion_criteria=(CRITERION,),
    )
    admission = record_admission(
        contract, approver="p2-human", approved_at=datetime.now(UTC).isoformat()
    )
    command = (str(check_python), "-I", "-B", CHECK_PATH)
    context = build_context_packet(
        root, source_paths=(TARGET, CHECK_PATH), budget_bytes=8192
    )
    journal = GuardedJournal(evidence / "state", root, prepared)
    lock = RepositoryLock(evidence / "state", timeout_seconds=300)
    adapter = RecordingAdapter(executable, evidence)
    summary: dict[str, object] = {
        "status": "unknown",
        "reason": "not_completed",
        "configuration": selected,
        "source_head": SOURCE_REVISION,
        "prepared_head": manifest["prepared_head"],
        "working_copy_retained": str(root),
        "private_raw_evidence": "private-native.json",
        "baseline": "UNKNOWN",
        "development": "UNKNOWN",
        "final": "UNKNOWN",
        "criteria_passed": False,
        "scope_clear": False,
        "protection_clear": False,
        "baseline_expected_behavioral_failure": False,
    }
    started = time.monotonic()
    try:
        lock.acquire()
        try:
            result = run_loop(
                adapter,
                contract,
                admission,
                context,
                policy=VerificationPolicy((command,), ".", 10),
                criterion_commands={CRITERION: (command,)},
                journal=journal,
                state_store=CurrentStateStore(evidence / "state"),
                budgets=TurnBudgets(120, 16384, 65536, 16384, 8192),
                limits=RetryLimits(0, 1, 4, 200),
                redactor=lambda text: "[private native text omitted from state]",
                max_output_bytes=65536,
                checkpoint_verification=True,
            )
        finally:
            lock.release()
        summary.update(
            status=result.status,
            reason=result.reason,
            command_boundaries=result.usage.commands,
            criteria_passed=bool(result.criteria)
            and all(item.passed for item in result.criteria),
        )
        if result.report is not None:
            summary["protection_clear"] = not result.report.protected_input_violations
            summary["baseline_expected_behavioral_failure"] = (
                len(result.report.profile.baseline.results) == 1
                and result.report.profile.baseline.results[0].exit_code == 1
                and not result.report.profile.baseline.results[0].timed_out
            )
        events = journal.read()
        summary.update(phase_facts(events, result.report))
        for event in events:
            if event.get("event") in ("scope_decision", "callback_scope_decision"):
                summary["scope_clear"] = event.get("proceed") is True
        summary["changed_paths"] = git(root, "diff", "--name-only").splitlines()
        (evidence / "target.patch").write_text(git(root, "diff", "--binary") + "\n")
        journal.check()
        if journal.guard_failure:
            summary.update(
                status="unknown" if result.status == "pass" else result.status,
                reason="independent_guard_violation"
                if result.status in ("pass", "unknown")
                else result.reason,
                scope_clear=False,
                protection_clear=False,
                criteria_passed=False,
                independent_guard="FAIL",
            )
        else:
            summary["independent_guard"] = "PASS"
        summary["history"] = summarize_execution(evidence / "state")
    except Exception as error:
        summary.update(status="unknown", reason=type(error).__name__)
    finally:
        summary.update(
            native=adapter.facts(), elapsed_seconds=time.monotonic() - started
        )
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-python-study", action="store_true")
    parser.add_argument("--prepare-copy", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--working-copy", type=Path)
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--check-python", type=Path)
    parser.add_argument("--codex-executable")
    args = parser.parse_args(argv)
    if not args.approve_python_study:
        parser.error(
            "read docs/python-study-example.md and pass --approve-python-study"
        )
    if any(
        value is None
        for value in (
            args.source,
            args.working_copy,
            args.evidence_directory,
            args.check_python,
        )
    ):
        parser.error("explicit source/copy/evidence/check-Python paths required")
    if args.prepare_copy:
        print(
            json.dumps(
                prepare(
                    args.source,
                    args.working_copy,
                    args.evidence_directory,
                    args.check_python,
                )
            )
        )
        return 0
    if not args.codex_executable or not Path(args.codex_executable).is_absolute():
        parser.error("trusted absolute Codex executable required")
    if any(
        value
        and key.startswith(("OPENAI_", "CODEX_", "CHATGPT_"))
        and key != "CODEX_HOME"
        for key, value in os.environ.items()
    ) or (
        os.environ.get("CODEX_HOME")
        and not Path(os.environ["CODEX_HOME"]).is_absolute()
    ):
        parser.error(
            "unsupported provider/auth environment overrides; native task not started"
        )
    # Existing configuration gate, unchanged; no model or effort override.
    try:
        selected = configuration(args.codex_executable)
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired):
        parser.error("configuration UNKNOWN or unsupported; native task not started")
    summary = execute(
        args.working_copy,
        args.evidence_directory,
        args.check_python,
        args.codex_executable,
        selected,
    )
    print(json.dumps(summary, sort_keys=True))
    return int(
        not (
            summary["status"] == "pass"
            and summary["criteria_passed"] is True
            and summary["scope_clear"] is True
            and summary["protection_clear"] is True
            and summary["baseline_expected_behavioral_failure"] is True
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

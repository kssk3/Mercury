"""One approved addition-CLI task in a retained copy of pinned python_study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
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
CHECKER = '''"""Seven protected black-box CLI cases; no project imports or dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
normal = [(('2', '3'), '5\\n'), (('-3', '1'), '-2\\n'), (('0', '0'), '0\\n')]
invalid = [(), ('2',), ('2', '3', '4'), ('word', '3')]
try:
    for args, expected in normal:
        result = subprocess.run([sys.executable, '-B', str(root/'chapter09/03.py'), *args], cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=1)
        if result.returncode != 0 or result.stdout != expected or result.stderr:
            print(json.dumps({'status':'SETUP_OR_NORMAL_FAILURE'}))
            sys.exit(3)
    failed = 0
    for args in invalid:
        result = subprocess.run([sys.executable, '-B', str(root/'chapter09/03.py'), *args], cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=1)
        failed += int(not (result.returncode == 2 and not result.stdout and result.stderr.strip() and 'Traceback' not in result.stderr))
    print(json.dumps({'normal_passed':3, 'invalid_passed':4-failed, 'cases':7}))
    sys.exit(int(bool(failed)))
except (OSError, subprocess.TimeoutExpired, UnicodeError):
    print(json.dumps({'status':'ENVIRONMENT_OR_TIMEOUT'}))
    sys.exit(3)
'''


def git(root: Path, *args: str) -> str:
    """Bounded local Git without inherited hooks, signing, or global config."""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
    )
    return subprocess.run(
        (
            "/usr/bin/git",
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
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def require_external(root: Path, evidence: Path) -> None:
    if (
        evidence == root
        or evidence.is_relative_to(root)
        or root.is_relative_to(evidence)
    ):
        raise ValueError("evidence must be separate from the working copy")


def prepare(
    source: Path, root: Path, evidence: Path, check_python: Path
) -> dict[str, str]:
    """Copy committed content only; source dirty work is never read into the copy."""
    source = source.resolve(strict=True)
    root, evidence = root.absolute(), evidence.absolute()
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
    rows = git(source, "ls-tree", "-r", "--full-tree", SOURCE_REVISION).splitlines()
    for row in rows:
        metadata, path = row.split("\t", 1)
        parts = Path(path).parts
        if (
            metadata.split()[0] not in ("100644", "100755")
            or any(part in (".codex", ".agents") for part in parts)
            or Path(path).name in ("AGENTS.md", "CODEX.md")
        ):
            raise ValueError("unsupported copied native settings or nonregular content")
    root.parent.mkdir(parents=True, exist_ok=True)
    git(
        root.parent,
        "clone",
        "--no-local",
        "--no-hardlinks",
        "--no-checkout",
        "--template=",
        "--",
        str(source),
        str(root),
    )
    root.chmod(0o700)
    git(root, "remote", "remove", "origin")
    git(root, "checkout", "--detach", SOURCE_REVISION)
    source_tree = git(root, "rev-parse", "HEAD^{tree}")
    if source_tree != git(source, "rev-parse", SOURCE_REVISION + "^{tree}"):
        raise ValueError("copied source tree mismatch")
    checker = root / CHECK_PATH
    checker.parent.mkdir()
    checker.write_text(CHECKER, encoding="utf-8")
    git(root, "add", "--", CHECK_PATH)
    git(root, "commit", "-m", "Add protected seven-case P2 preparation checker")
    if git(root, "status", "--porcelain") or git(root, "remote"):
        raise ValueError("prepared copy must be clean and have no remotes")
    evidence.mkdir(mode=0o700, parents=True)
    manifest = {
        "source_head": SOURCE_REVISION,
        "source_tree": source_tree,
        "prepared_head": git(root, "rev-parse", "HEAD"),
        "root": str(root),
        "check_python": str(check_python),
        "checker_sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
    }
    (evidence / "preparation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


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
    if (
        manifest.get("root") != str(root)
        or manifest.get("source_head") != SOURCE_REVISION
        or manifest.get("check_python") != str(check_python)
        or manifest.get("prepared_head") != git(root, "rev-parse", "HEAD")
        or manifest.get("checker_sha256")
        != hashlib.sha256((root / CHECK_PATH).read_bytes()).hexdigest()
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
    command = (str(check_python), "-B", CHECK_PATH)
    context = build_context_packet(
        root, source_paths=(TARGET, CHECK_PATH), budget_bytes=8192
    )
    journal = EventJournal(evidence / "state")
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

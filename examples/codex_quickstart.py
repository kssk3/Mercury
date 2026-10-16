"""One explicitly approved Codex task in an owned disposable Git fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from agent_harness.adapter import CodexCLIAdapter, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.evidence import EvidenceReport
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits
from agent_harness.runner import CommandResult
from agent_harness.state import CurrentStateStore

BUG = "def discount(price, percent):\n    return price * (1 - percent / 50)\n"
CHECK = (
    "from calculator import discount\n"
    "for price, percent, expected in ((100, 10, 90), (200, 25, 150), (0, 10, 0)):\n"
    "    if discount(price, percent) != expected:\n"
    "        raise SystemExit('discount check failed')\n"
)
METADATA_TIMEOUT = 10
METADATA_BYTES = 262144
METADATA_REAP_TIMEOUT = 2


def git(root: Path, *args: str) -> None:
    """Only fixture-local initialization, with external hooks/config disabled."""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
    )
    subprocess.run(
        (
            "git",
            "-c",
            f"core.hooksPath={root.parent / 'empty-hooks'}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture User",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ),
        env=env,
        check=True,
        capture_output=True,
        timeout=10,
    )


def metadata(executable: str) -> list[dict[str, object]]:
    """Read metadata only, with one deadline and bounded, discarded responses."""
    requests = [
        (
            "initialize",
            {
                "clientInfo": {"name": "mercury_example", "version": "1"},
                "capabilities": {
                    "optOutNotificationMethods": [
                        "account/updated",
                        "remoteControl/status/changed",
                    ]
                },
            },
        ),
        ("config/read", {"includeLayers": False}),
        ("configRequirements/read", {}),
        ("account/read", {"refreshToken": False}),
    ]
    deadline = time.monotonic() + METADATA_TIMEOUT
    process = subprocess.Popen(
        (executable, "--strict-config", "app-server"),
        cwd=os.path.sep,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=True,
    )
    try:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ValueError("metadata pipes unavailable")
        responses: list[dict[str, object]] = []
        buffered = b""
        received = 0

        def request(index: int) -> bytes:
            method, params = requests[index]
            return (
                json.dumps({"id": index + 1, "method": method, "params": params}) + "\n"
            ).encode()

        outgoing = request(0)
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            selector.register(process.stderr, selectors.EVENT_READ, "error")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("metadata deadline")
                for key, _ in selector.select(remaining):
                    if key.data == "input":
                        outgoing = outgoing[os.write(key.fd, outgoing) :]
                        if not outgoing:
                            selector.unregister(key.fileobj)
                        continue
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    received += len(chunk)
                    if received > METADATA_BYTES or key.data == "error":
                        raise ValueError("metadata unavailable")
                    buffered += chunk
                    while b"\n" in buffered:
                        line, buffered = buffered.split(b"\n", 1)
                        response = json.loads(line)
                        if (
                            outgoing
                            or not isinstance(response, dict)
                            or set(response) != {"id", "result"}
                            or type(response["id"]) is not int
                            or response["id"] != len(responses) + 1
                            or not isinstance(response["result"], dict)
                            or len(responses) == len(requests)
                        ):
                            raise ValueError("unexpected metadata response")
                        responses.append(response["result"])
                        if len(responses) == len(requests):
                            process.stdin.close()
                        else:
                            outgoing = request(len(responses))
                            if len(responses) == 1:
                                outgoing = b'{"method":"initialized"}\n' + outgoing
                            selector.register(
                                process.stdin, selectors.EVENT_WRITE, "input"
                            )
            if buffered or len(responses) != len(requests):
                raise ValueError("incomplete metadata")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                raise ValueError("metadata process failed")
        return responses
    finally:
        # Native credential refresh/persistence remains the CLI's responsibility.
        # Reap the owned probe group on error; never retain raw metadata or stderr.
        try:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=METADATA_REAP_TIMEOUT)
        finally:
            for cleanup_stream in (process.stdin, process.stdout, process.stderr):
                if cleanup_stream is not None:
                    cleanup_stream.close()


def require_standard_configuration(data: dict[str, object], *, effective: bool) -> None:
    """Refuse routing that this deliberately narrow example cannot establish."""
    provider = data.get("model_provider", None if effective else "openai")
    if effective and "model_provider" in data and provider is None:
        provider = "openai"
    if provider != "openai":
        raise ValueError("unsupported provider")
    for key in ("model_providers", "profiles"):
        if key in data and data[key] is not None and data[key] != {}:
            raise ValueError("unsupported provider or profile definitions")
    for key in (
        "profile",
        "openai_base_url",
        "experimental_realtime_ws_base_url",
    ):
        if data.get(key) is not None:
            raise ValueError("unsupported routing override")
    # config/read materializes these defaults even without saved overrides.
    for key, default in (
        ("chatgpt_base_url", "https://chatgpt.com/backend-api/"),
        ("project_root_markers", [".git"]),
    ):
        if data.get(key) is not None and (not effective or data[key] != default):
            raise ValueError("unsupported routing override")
    if data.get("forced_login_method") not in (None, "chatgpt"):
        raise ValueError("unsupported authentication")
    if data.get("cli_auth_credentials_store") not in (None, "file", "keyring", "auto"):
        raise ValueError("unsupported credential storage")


def configuration(executable: str) -> dict[str, object]:
    """Inspect accepted config without initiating login or writing config/rules."""
    try:
        (Path(os.path.sep) / ".codex" / "config.toml").lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("filesystem-root project configuration unsupported")
    config = (
        Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
        / "config.toml"
    )
    with config.open("rb") as stream:
        raw = stream.read(METADATA_BYTES + 1)
    if len(raw) > METADATA_BYTES:
        raise ValueError("configuration too large")
    saved = tomllib.loads(raw.decode("utf-8"))
    initialized, configured, requirements, account = metadata(executable)
    if not all(
        isinstance(initialized.get(key), str) and initialized[key]
        for key in ("userAgent", "platformFamily", "platformOs")
    ):
        raise ValueError("initialization metadata unavailable")
    data = configured.get("config")
    if not isinstance(data, dict):
        raise ValueError("effective configuration unavailable")
    require_standard_configuration(saved, effective=False)
    require_standard_configuration(data, effective=True)
    if "requirements" not in requirements or requirements["requirements"] is not None:
        raise ValueError("managed configuration unsupported")
    identity = account.get("account")
    if (
        not isinstance(identity, dict)
        or identity.get("type") != "chatgpt"
        or account.get("requiresOpenaiAuth") is not True
    ):
        raise ValueError("saved ChatGPT account required")
    for key in ("model", "model_reasoning_effort"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError("ambiguous model setting")
    return {
        "model": data.get("model") or "UNKNOWN",
        "effort": data.get("model_reasoning_effort") or "UNKNOWN",
        "provenance": "effective config/read; native events authoritative when available",
    }


class RecordingAdapter(CodexCLIAdapter):
    """Retain private native receipts outside the fixture; never print raw text."""

    def __init__(self, executable: str, evidence: Path) -> None:
        super().__init__(executable)
        self.evidence = evidence
        self.result: CodexTurnResult | None = None
        self.attempts = 0

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> CodexTurnResult:
        if self.attempts:
            raise RuntimeError("one native attempt only; retry/resume refused")
        self.attempts += 1
        self.result = super().run(
            contract, admission, context, budgets=budgets, sandbox=sandbox
        )
        (self.evidence / "private-native.json").write_text(
            json.dumps(asdict(self.result), ensure_ascii=True),
            encoding="utf-8",
        )
        return self.result

    def facts(self) -> dict[str, object]:
        if self.result is None:
            return {"attempts": self.attempts, "status": "UNKNOWN", "usage": "UNKNOWN"}
        usage: object = "UNKNOWN"
        observed_model: object = "UNKNOWN"
        for line in self.result.stdout.text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "turn.completed" and isinstance(
                event.get("usage"), dict
            ):
                raw = event["usage"]
                usage = {
                    key: raw[key]
                    for key in ("input_tokens", "cached_input_tokens", "output_tokens")
                    if type(raw.get(key)) is int and raw[key] >= 0
                } or "UNKNOWN"
            if isinstance(event.get("model"), str):
                observed_model = event["model"]
        return {
            "attempts": self.attempts,
            "status": self.result.process_status,
            "exit_code": self.result.exit_code,
            "elapsed_seconds": self.result.duration_seconds,
            "usage": usage,
            "observed_model": observed_model,
            "retention": {
                key: {"bytes": value.retained_bytes, "truncated": value.truncated}
                for key, value in (
                    ("stdout", self.result.stdout),
                    ("stderr", self.result.stderr),
                    ("final", self.result.final_message),
                )
                if value is not None
            },
            "final_output_status": self.result.final_output_status,
        }


def command_facts(results: tuple[CommandResult, ...]) -> list[dict[str, object]]:
    """Match the journal's safe command identity and returned process facts."""
    return [
        {
            "command_sha256": hashlib.sha256(
                json.dumps(
                    item.command, separators=(",", ":"), ensure_ascii=True
                ).encode()
            ).hexdigest(),
            "exit_code": item.exit_code,
            "timed_out": item.timed_out,
        }
        for item in results
    ]


def command_status(value: object) -> str:
    """Incomplete or timed-out checks do not establish a phase result."""
    if not isinstance(value, list) or not value:
        return "UNKNOWN"
    rows = cast(list[object], value)
    if any(
        not isinstance(row, dict)
        or type(row.get("exit_code")) is not int
        or row.get("timed_out") is not False
        for row in rows
    ):
        return "UNKNOWN"
    return (
        "FAIL"
        if any(row["exit_code"] != 0 for row in cast(list[dict[str, object]], rows))
        else "PASS"
    )


def phase_facts(
    events: list[dict[str, object]], report: EvidenceReport | None
) -> dict[str, str]:
    """Use completed checkpoint/report evidence, never final intent alone."""
    facts = {"baseline": "UNKNOWN", "development": "UNKNOWN", "final": "UNKNOWN"}
    phases: dict[str, str] = {}
    if report is not None:
        facts["baseline"] = command_status(
            command_facts(report.profile.baseline.results)
        )
    for event in events:
        attempt = event.get("attempt_id")
        if not isinstance(attempt, str):
            continue
        if event.get("event") == "verification_checkpoint":
            facts["baseline"] = command_status(event.get("baseline"))
            if (
                event.get("pending_phase") == "final"
                and phases.get(attempt) == "development"
            ):
                facts["development"] = command_status(event.get("completed_results"))
        elif event.get("event") == "verification_phase_intent":
            phase = event.get("phase")
            if phase in ("development", "final"):
                phases[attempt] = phase
        elif (
            event.get("event") == "attempt_verification"
            and report is not None
            and attempt in phases
            and event.get("repository_root") == str(report.repository_root)
            and event.get("target_revision") == report.target_revision
            and event.get("overall_kind") == report.overall_kind.value
            and event.get("commands") == command_facts(report.profile.results)
            and event.get("baseline_commands")
            == command_facts(report.profile.baseline.results)
        ):
            status = command_status(event["commands"])
            facts[phases[attempt]] = (
                "FAIL" if report.protected_input_violations else status
            )
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-codex-demo", action="store_true")
    parser.add_argument(
        "--codex-executable", default="codex", help="trusted external executable"
    )
    parser.add_argument(
        "--evidence-parent",
        type=Path,
        default=Path(tempfile.gettempdir()),
        help="existing directory outside the fixture; owned child receipts retained",
    )
    args = parser.parse_args(argv)
    if not args.approve_codex_demo:
        parser.error("read docs/codex-quickstart.md and pass --approve-codex-demo")
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
            "remove API-key overrides and other provider/auth environment overrides; "
            "only absolute CODEX_HOME is supported"
        )
    executable = shutil.which(args.codex_executable)
    if executable is None:
        parser.error("trusted Codex executable unavailable")
    # Metadata only; do not store or display raw config/account/error output.
    try:
        selected_config = configuration(executable)
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired):
        parser.error(
            "standard OpenAI/ChatGPT configuration preflight UNKNOWN or unsupported; "
            "native task not started"
        )
    evidence = Path(
        tempfile.mkdtemp(prefix="mercury-codex-", dir=args.evidence_parent)
    ).resolve()
    evidence.chmod(0o700)
    started = time.monotonic()
    summary: dict[str, object] = {
        "mode": "native-codex",
        "status": "unknown",
        "reason": "not_completed",
        "configuration": selected_config,
        "authentication": "saved ChatGPT login",
        "evidence_directory": str(evidence),
        "private_raw_evidence": "private-native.json",
        "baseline": "UNKNOWN",
        "development": "UNKNOWN",
        "final": "UNKNOWN",
        "criteria_passed": False,
        "scope_clear": False,
        "protection_clear": False,
        "changed_paths": [],
        "native": {"attempts": 0, "usage": "UNKNOWN"},
    }
    adapter = RecordingAdapter(executable, evidence)
    try:
        with tempfile.TemporaryDirectory(prefix="mercury-discount-") as directory:
            scratch = Path(directory).resolve()
            root = scratch / "repository"
            root.mkdir()
            (scratch / "empty-hooks").mkdir()
            (root / "calculator.py").write_text(BUG, encoding="utf-8")
            (root / "check.py").write_text(CHECK, encoding="utf-8")
            git(root, "init", "--template=", "--initial-branch=demo")
            git(root, "add", "calculator.py", "check.py")
            git(root, "commit", "-m", "Create disposable discount fixture")
            criterion = "discount(100,10)=90, discount(200,25)=150, discount(0,10)=0"
            contract = TaskContract(
                goal="Fix discount in calculator.py only. Work only in this disposable fixture. "
                "Do not use extra agents, services, network, or installs. Do not edit check.py "
                "or Git metadata; do not commit. Return a brief result.",
                allowed_paths=("calculator.py",),
                protected_paths=("check.py",),
                completion_criteria=(criterion,),
            )
            admission = record_admission(
                contract,
                approver="demo-human",
                approved_at=datetime.now(UTC).isoformat(),
            )
            command = (sys.executable, "-B", "check.py")
            policy = VerificationPolicy((command,), ".", 5)
            context = build_context_packet(
                root, source_paths=("calculator.py", "check.py"), budget_bytes=4096
            )
            state = evidence / "state"
            lock = RepositoryLock(state, timeout_seconds=260)
            journal = EventJournal(state)
            lock.acquire()
            try:
                result = run_loop(
                    adapter,
                    contract,
                    admission,
                    context,
                    policy=policy,
                    criterion_commands={criterion: (command,)},
                    journal=journal,
                    state_store=CurrentStateStore(state),
                    budgets=TurnBudgets(120, 16384, 65536, 16384, 8192),
                    limits=RetryLimits(0, 1, 4, 180),
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
                summary[
                    "protection_clear"
                ] = not result.report.protected_input_violations
            events = journal.read()
            summary.update(phase_facts(events, result.report))
            for event in events:
                if event.get("event") in {"scope_decision", "callback_scope_decision"}:
                    summary["scope_clear"] = event.get("proceed", False)
                    delta = event.get("delta")
                    if isinstance(delta, dict):
                        summary["changed_paths"] = sorted(
                            set(
                                delta.get("added", [])
                                + delta.get("modified", [])
                                + delta.get("deleted", [])
                            )
                        )
        summary["fixture_removed"] = True
    except Exception as error:
        summary.update(status="unknown", reason=type(error).__name__)
    finally:
        summary["native"] = adapter.facts()
        summary["elapsed_seconds"] = time.monotonic() - started
        (evidence / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, sort_keys=True))
    return int(summary["status"] != "pass" or summary["criteria_passed"] is not True)


if __name__ == "__main__":
    raise SystemExit(main())

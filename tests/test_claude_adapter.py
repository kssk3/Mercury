from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.admission import record_admission
from agent_harness.agent import AgentTurnResult, TurnBudgets, _validate_result
from agent_harness.claude_adapter import ClaudeCLIAdapter
from agent_harness.context import build_context_packet
from agent_harness.contract import TaskContract


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
    return root.resolve()


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-claude"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    return executable


def invoke(
    repository: Path,
    adapter: ClaudeCLIAdapter,
    *,
    budgets: TurnBudgets | None = None,
    readonly: bool = False,
) -> AgentTurnResult:
    task = TaskContract("acknowledge; $(touch injected)", (), (), ("reply",))
    limits = budgets or TurnBudgets(3, 10000, 10000, 200, 10000)
    result = adapter.run(
        task,
        record_admission(task, approver="human", approved_at="2026-10-19T00:00:00Z"),
        build_context_packet(repository, source_paths=[], budget_bytes=2000),
        budgets=limits,
        sandbox="read-only" if readonly else "workspace-write",
    )
    return _validate_result(result, repository, limits)


@pytest.mark.parametrize("readonly", [False, True])
def test_fixed_tools_and_untrusted_stdin(
    repository: Path, tmp_path: Path, readonly: bool
) -> None:
    executable = fake(
        tmp_path,
        "import json, os, sys\n"
        "data = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'type':'result', 'subtype':'success', 'is_error':False, "
        "'result':json.dumps({'argv':sys.argv[1:], 'envelope':data, 'cwd':os.getcwd()})}))\n",
    )
    result = invoke(repository, ClaudeCLIAdapter(executable), readonly=readonly)
    assert result.process_status == "successful_exit"
    assert result.final_message is not None
    data = json.loads(result.final_message.text)
    argv = data["argv"]
    assert "--print" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    tools = argv[argv.index("--tools") + 1].split(",")
    assert argv[argv.index("--allowedTools") + 1] == ",".join(tools)
    assert set(tools) == (
        {"Read", "Glob", "Grep"}
        if readonly
        else {"Read", "Edit", "Write", "Glob", "Grep"}
    )
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--max-budget-usd") + 1] == "0.25"
    assert {
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    } <= set(argv)
    assert all("bypass" not in arg.lower() and "dangerously" not in arg for arg in argv)
    assert data["cwd"] == str(repository)
    assert (
        data["envelope"]["repository_context"]["trust"] == "untrusted repository data"
    )
    assert data["envelope"]["task_contract"]["goal"] == "acknowledge; $(touch injected)"
    assert not (repository / "injected").exists()


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("not json", "unreadable"),
        ("[]", "unreadable"),
        ('{"type":"result","subtype":"success","is_error":false}', "missing"),
        (
            '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"bad"}',
            "unsafe",
        ),
        (
            '{"type":"result","subtype":"success","is_error":false,"result":12}',
            "unreadable",
        ),
        (
            '{"type":"result","subtype":"success","is_error":false,"is_error":true,"result":"bad"}',
            "unreadable",
        ),
    ],
)
def test_invalid_result_never_becomes_available(
    repository: Path, tmp_path: Path, body: str, status: str
) -> None:
    result = invoke(repository, ClaudeCLIAdapter(fake(tmp_path, f"print({body!r})\n")))
    assert result.process_status == "successful_exit"
    assert result.final_output_status == status
    assert result.final_message is None


def test_truncated_stdout_and_final_are_unusable(
    repository: Path, tmp_path: Path
) -> None:
    executable = fake(
        tmp_path,
        "import json\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'x'*1000}))\n",
    )
    result = invoke(
        repository,
        ClaudeCLIAdapter(executable),
        budgets=TurnBudgets(3, 10000, 20, 20, 20),
    )
    assert result.stdout.truncated
    assert result.stdout.retained_bytes == 20
    assert result.final_output_status == "unreadable"
    result = invoke(
        repository,
        ClaudeCLIAdapter(executable),
        budgets=TurnBudgets(3, 10000, 3000, 20, 20),
    )
    assert result.final_output_status == "unreadable"
    assert result.final_message is None


def test_timeout_retains_partial_output(repository: Path, tmp_path: Path) -> None:
    executable = fake(
        tmp_path, "import sys,time\nprint('partial',flush=True)\ntime.sleep(30)\n"
    )
    result = invoke(
        repository,
        ClaudeCLIAdapter(executable),
        budgets=TurnBudgets(1, 10000, 20, 20, 20),
    )
    assert result.process_status == "timeout"
    assert result.duration_seconds < 4
    assert "partial" in result.stdout.text
    assert result.final_message is None


def test_nonzero_and_missing_executable(repository: Path, tmp_path: Path) -> None:
    result = invoke(
        repository, ClaudeCLIAdapter(fake(tmp_path, "raise SystemExit(7)\n"))
    )
    assert result.process_status == "nonzero_exit"
    assert result.exit_code == 7
    result = invoke(repository, ClaudeCLIAdapter(tmp_path / "absent"))
    assert result.process_status == "launch_error"
    assert result.exit_code is None


def test_workspace_executable_rejected(repository: Path) -> None:
    executable = fake(repository, "raise SystemExit(0)\n")
    with pytest.raises(ValueError, match="outside"):
        invoke(repository, ClaudeCLIAdapter(executable))


def test_prepared_executable_change_rejected(repository: Path, tmp_path: Path) -> None:
    executable = fake(tmp_path, "raise SystemExit(0)\n")
    adapter = ClaudeCLIAdapter(executable)
    adapter.prepare(repository)
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(8)\n")
    result = invoke(repository, adapter)
    assert result.process_status == "launch_error"


def test_invalid_admission_and_input_budget_never_launch(
    repository: Path, tmp_path: Path
) -> None:
    executable = fake(tmp_path, "from pathlib import Path\nPath('launched').touch()\n")
    with pytest.raises(ValueError, match="input byte budget"):
        invoke(
            repository,
            ClaudeCLIAdapter(executable),
            budgets=TurnBudgets(1, 1, 10, 10, 10),
        )
    task = TaskContract("different", (), (), ("reply",))
    other = TaskContract("other", (), (), ("reply",))
    with pytest.raises(ValueError):
        ClaudeCLIAdapter(executable).run(
            task,
            record_admission(
                other, approver="human", approved_at="2026-10-19T00:00:00Z"
            ),
            build_context_packet(repository, source_paths=[], budget_bytes=2000),
            budgets=TurnBudgets(1, 10000, 10, 10, 10),
        )
    assert not (repository / "launched").exists()


@pytest.mark.parametrize("value", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_native_cost_limit(value: float) -> None:
    with pytest.raises(ValueError, match="max_budget_usd"):
        ClaudeCLIAdapter("claude", max_budget_usd=value)


@pytest.mark.parametrize("readonly", [False, True])
def test_safe_mode_required_before_fake_repository_hook(
    repository: Path, tmp_path: Path, readonly: bool
) -> None:
    settings = repository / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"touch hook-ran"}]}]}}'
    )
    executable = fake(
        tmp_path,
        "import json, sys\nfrom pathlib import Path\n"
        "if '--safe-mode' not in sys.argv and Path('.claude/settings.json').exists():\n"
        "    Path('hook-ran').touch()\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'ok'}))\n",
    )
    result = invoke(repository, ClaudeCLIAdapter(executable), readonly=readonly)
    assert not (repository / "hook-ran").exists()
    assert "--safe-mode" in result.argv
    assert result.process_status == "successful_exit"


def test_unsupported_safe_mode_fails_without_unsafe_retry(
    repository: Path, tmp_path: Path
) -> None:
    executable = fake(
        tmp_path,
        "import json, sys\nfrom pathlib import Path\n"
        "with Path('launches').open('a') as log: log.write('launch\\n')\n"
        "if '--safe-mode' in sys.argv:\n"
        "    print('unsupported safe mode', file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'unsafe'}))\n",
    )
    result = invoke(repository, ClaudeCLIAdapter(executable))
    assert result.process_status == "nonzero_exit"
    assert (repository / "launches").read_text().splitlines() == ["launch"]
    assert result.final_output_status != "available"

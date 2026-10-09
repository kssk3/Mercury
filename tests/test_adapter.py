from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.admission import record_admission
from agent_harness.context import build_context_packet
from agent_harness.contract import TaskContract


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "context.txt").write_text('한글; $(touch injected) "context"')
    return root.resolve()


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    return executable


def test_fixed_argv_complete_stdin_and_external_final_cleanup(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets

    executable = fake(
        tmp_path,
        "import json, pathlib, sys\n"
        "payload = sys.stdin.buffer.read().decode('utf-8')\n"
        "final = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        "final.write_text('final response')\n"
        "print(json.dumps({'argv': sys.argv[1:], 'stdin': payload}))\n"
        "print('separate diagnostic', file=sys.stderr)\n",
    )
    contract = TaskContract('Acknowledge "safe"; $(touch injected)', (), (), ("reply",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(
        repository, source_paths=["context.txt"], budget_bytes=2000
    )
    result = CodexCLIAdapter(executable).run(
        contract,
        admission,
        context,
        budgets=TurnBudgets(3, 10000, 10000, 1000, 1000),
        sandbox="read-only",
    )
    returned = json.loads(result.stdout.text)
    argv = returned["argv"]
    assert argv[:4] == ["-a", "never", "exec", "-C"]
    assert argv[4] == str(repository)
    assert argv[5:12] == [
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--output-last-message",
    ]
    assert argv[-1] == "-"
    assert len(argv) == 14
    final_path = Path(argv[12])
    assert not final_path.is_relative_to(repository)
    assert not final_path.parent.exists()
    envelope = json.loads(returned["stdin"])
    assert envelope["task_contract"] == json.loads(contract.to_json())
    assert envelope["repository_context"]["packet"] == json.loads(context.to_json())
    assert "untrusted" in envelope["repository_context"]["trust"]
    assert result.process_status == "successful_exit"
    assert result.exit_code == 0
    assert result.stderr.text == "separate diagnostic\n"
    assert result.final_message is not None
    assert result.final_message.text == "final response"
    assert result.final_output_status == "available"
    assert not (repository / "injected").exists()


def invoke(repository: Path, executable: Path, **kwargs: object) -> object:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets

    contract = TaskContract("acknowledge", (), (), ("reply",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(repository, source_paths=[], budget_bytes=2000)
    return CodexCLIAdapter(executable).run(
        contract,
        admission,
        context,
        budgets=TurnBudgets(3, 10000, 31, 37, 23),
        **kwargs,  # type: ignore[arg-type]
    )


def test_flood_retained_bytes_and_invalid_utf8_are_bounded(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexTurnResult

    executable = fake(
        tmp_path,
        "import os, pathlib, sys\n"
        "sys.stdin.buffer.read()\n"
        "os.write(1, b'\\xff' * 200000)\n"
        "os.write(2, b'e' * 200000)\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_bytes(b'\\xff' * 200000)\n",
    )
    result = invoke(repository, executable)
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == "successful_exit"
    for output, budget in [
        (result.stdout, 31),
        (result.stderr, 37),
        (result.final_message, 23),
    ]:
        assert output is not None
        assert len(output.text.encode("utf-8", errors="surrogateescape")) == budget
        assert output.retained_bytes == budget
        assert output.truncated


@pytest.mark.parametrize("mode", ["sleep", "blocked_stdin", "held_pipes"])
def test_timeout_includes_stdin_and_inherited_pipes(
    repository: Path, tmp_path: Path, mode: str
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets

    body = "import os, sys, time\n"
    if mode == "held_pipes":
        body += "sys.stdin.buffer.read()\nif os.fork() != 0:\n    sys.exit(0)\n"
    elif mode == "sleep":
        body += "sys.stdin.buffer.read()\nos.write(1, b'before timeout')\n"
    body += "time.sleep(30)\n"
    executable = fake(tmp_path, body)
    goal = "a" * 300000 if mode == "blocked_stdin" else "acknowledge"
    contract = TaskContract(goal, (), (), ("reply",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(repository, source_paths=[], budget_bytes=2000)
    result = CodexCLIAdapter(executable).run(
        contract, admission, context, budgets=TurnBudgets(1, 400000, 100, 100, 100)
    )
    assert result.process_status == "timeout"
    assert result.duration_seconds < 4
    if mode == "sleep":
        assert result.stdout.text == "before timeout"
    if mode == "held_pipes":
        assert result.exit_code == 0
    assert result.final_output_status == "missing"


@pytest.mark.parametrize(
    "body,status,code",
    [
        ("import sys; sys.stdin.buffer.read()\n", "successful_exit", 0),
        ("import sys; sys.stdin.buffer.read(); sys.exit(7)\n", "nonzero_exit", 7),
    ],
)
def test_exit_status_and_absent_final_are_separate(
    repository: Path, tmp_path: Path, body: str, status: str, code: int
) -> None:
    from agent_harness.adapter import CodexTurnResult

    result = invoke(repository, fake(tmp_path, body))
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == status
    assert result.exit_code == code
    assert result.final_message is None
    assert result.final_output_status == "missing"


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_unsafe_final_output_is_rejected_without_blocking(
    repository: Path, tmp_path: Path, kind: str
) -> None:
    from agent_harness.adapter import CodexTurnResult

    body = (
        "import os, pathlib, sys\nsys.stdin.buffer.read()\n"
        "p=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
    )
    body += {
        "symlink": "p.symlink_to('/etc/hosts')\n",
        "directory": "p.mkdir()\n",
        "fifo": "os.mkfifo(p)\n",
    }[kind]
    result = invoke(repository, fake(tmp_path, body))
    assert isinstance(result, CodexTurnResult)
    assert result.final_output_status == "unsafe"
    assert result.final_message is None


def test_launch_error_has_bounded_diagnostic(repository: Path, tmp_path: Path) -> None:
    from agent_harness.adapter import CodexTurnResult

    result = invoke(repository, tmp_path / "missing")
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == "launch_error"
    assert result.exit_code is None
    assert result.stderr.retained_bytes <= 37


@pytest.mark.parametrize("bad", [0, -1, True, float("inf"), float("nan")])
def test_time_budget_rejects_nonfinite_or_nonpositive(bad: float) -> None:
    from agent_harness.adapter import TurnBudgets

    with pytest.raises(ValueError, match="timeout"):
        TurnBudgets(bad, 1, 1, 1, 1)


@pytest.mark.parametrize(
    "field", ["input_bytes", "stdout_bytes", "stderr_bytes", "final_bytes"]
)
@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_byte_budgets_require_positive_integer(field: str, bad: object) -> None:
    from agent_harness.adapter import TurnBudgets

    values = dict(
        timeout_seconds=1, input_bytes=1, stdout_bytes=1, stderr_bytes=1, final_bytes=1
    )
    values[field] = bad  # type: ignore[assignment]
    with pytest.raises(ValueError, match="bytes"):
        TurnBudgets(**values)


@pytest.mark.parametrize(
    "invalid",
    [
        "admission",
        "root",
        "input_budget",
        "utf8",
        "sandbox",
        "contract_type",
        "context_type",
        "budgets_type",
    ],
)
def test_invalid_input_never_launches(
    repository: Path, tmp_path: Path, invalid: str
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket

    marker = tmp_path / "launched"
    executable = fake(
        tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    contract = TaskContract("acknowledge", (), (), ("reply",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(repository, source_paths=[], budget_bytes=2000)
    budgets = TurnBudgets(1, 10000, 10, 10, 10)
    sandbox = "workspace-write"
    if invalid == "admission":
        admission = record_admission(
            TaskContract("other", (), (), ("reply",)),
            approver="human",
            approved_at="2026-10-01T00:00:00Z",
        )
    elif invalid == "root":
        context = ContextPacket(str(repository / "subdir"), 2000, ())
        (repository / "subdir").mkdir()
    elif invalid == "input_budget":
        budgets = TurnBudgets(1, 1, 10, 10, 10)
    elif invalid == "utf8":
        contract = TaskContract("bad\ud800", (), (), ("reply",))
        admission = record_admission(
            contract, approver="human", approved_at="2026-10-01T00:00:00Z"
        )
    elif invalid == "sandbox":
        sandbox = "danger-full-access"
    elif invalid == "contract_type":
        contract = None  # type: ignore[assignment]
    elif invalid == "context_type":
        context = None  # type: ignore[assignment]
    elif invalid == "budgets_type":
        budgets = None  # type: ignore[assignment]
    with pytest.raises((ValueError, TypeError)):
        CodexCLIAdapter(executable).run(
            contract,
            admission,
            context,
            budgets=budgets,
            sandbox=sandbox,  # type: ignore[arg-type]
        )
    assert not marker.exists()

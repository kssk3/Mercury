from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


def invoke(repository: Path, executable: Path | str, **kwargs: object) -> object:
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


def test_timeout_includes_startup_and_blocked_stdin(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets

    executable = fake(tmp_path, "import time\ntime.sleep(30)\n")
    contract = TaskContract("a" * 300000, (), (), ("reply",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(repository, source_paths=[], budget_bytes=2000)
    result = CodexCLIAdapter(executable).run(
        contract, admission, context, budgets=TurnBudgets(1, 400000, 100, 100, 100)
    )
    assert result.process_status == "timeout"
    assert result.duration_seconds < 4
    assert result.final_output_status == "missing"


@contextmanager
def ready_timeout_process(
    tmp_path: Path, mode: str
) -> Iterator[subprocess.Popen[bytes]]:
    ready_read, ready_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    marker = tmp_path / "descendant-marker"
    script = "import os, pathlib, sys, time\n"
    if mode == "held_pipes":
        script += "if os.fork() != 0:\n    sys.exit(0)\n"
    else:
        script += "os.write(1, b'before timeout')\n"
    script += (
        f"os.write({ready_write}, b'R')\nos.close({ready_write})\n"
        "sys.stdin.buffer.read()\n"
        "time.sleep(2)\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    try:
        process = subprocess.Popen(
            (sys.executable, "-c", script),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(ready_write,),
            start_new_session=True,
        )
        os.close(ready_write)
        ready_write = -1
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            selector.register(ready_read, selectors.EVENT_READ)
            assert selector.select(max(0, deadline - time.monotonic())), (
                "child did not become ready"
            )
            assert os.read(ready_read, 1) == b"R"
        if mode == "held_pipes":
            assert process.wait(timeout=max(0, deadline - time.monotonic())) == 0
        yield process
    finally:
        os.close(ready_read)
        if ready_write != -1:
            os.close(ready_write)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


@pytest.mark.parametrize("mode", ["sleep", "held_pipes"])
def test_ready_child_timeout_preserves_output_exit_and_descendant_cleanup(
    tmp_path: Path, mode: str
) -> None:
    from agent_harness import adapter

    with ready_timeout_process(tmp_path, mode) as process:
        stdout = adapter._Capture(100)
        stderr = adapter._Capture(100)
        started = time.monotonic()
        timed_out = adapter._communicate_bounded(
            process, b"complete prompt", stdout, stderr, started, 1
        )
        assert timed_out
        assert time.monotonic() - started < 4
        if mode == "sleep":
            assert stdout.result().text == "before timeout"
        else:
            assert process.returncode == 0
        assert adapter._read_final(tmp_path / "final.txt", 100) == (None, "missing")
        assert process.stdin is not None and process.stdin.closed
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed
        time.sleep(2.1)
        assert not (tmp_path / "descendant-marker").exists()


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


def test_hardlinked_final_response_is_rejected_and_descriptor_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from agent_harness import adapter

    original = tmp_path / "unrelated.txt"
    original.write_text("unrelated private contents")
    final = tmp_path / "final.txt"
    final.hardlink_to(original)
    actual_open = os.open
    descriptors: list[int] = []

    def tracked_open(path: Path, flags: int) -> int:
        descriptor = actual_open(path, flags)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", tracked_open)
    capture, status = adapter._read_final(final, 100)
    assert status == "unsafe"
    assert capture is None
    assert original.read_text() == "unrelated private contents"
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_native_selection_survives_writable_path_shadow(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket

    tools = repository / "tools"
    tools.mkdir()
    marker = tmp_path / "trusted-launches"
    planted = tmp_path / "planted-launches"
    shadow = tools / "fake-codex"
    attacker = f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(planted)!r}).touch()\n"
    executable = fake(
        tmp_path,
        "import pathlib,sys\nsys.stdin.read()\n"
        f"p=pathlib.Path({str(marker)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        f"s=pathlib.Path({str(shadow)!r}); s.write_text({attacker!r}); s.chmod(0o700)\n"
        "sys.exit(1)\n",
    )
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(tools), str(tmp_path), os.environ["PATH"]))
    )
    adapter = CodexCLIAdapter("fake-codex")
    task = TaskContract("selection continuity", (), (), ("reply",))
    admitted = record_admission(
        task, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    results = [
        adapter.run(
            task,
            admitted,
            ContextPacket(str(repository), 1000, ()),
            budgets=TurnBudgets(3, 10000, 100, 100, 100),
        )
        for _ in range(2)
    ]
    assert shadow.is_file()
    assert marker.read_text() == "xx"
    assert not planted.exists()
    assert all(
        r.argv[0] == str(executable.resolve()) and r.process_status == "nonzero_exit"
        for r in results
    )


@pytest.mark.parametrize(
    "kind", ["absolute", "path_name", "external_alias", "workspace_alias"]
)
def test_native_selection_rejects_workspace_candidates(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    marker = tmp_path / "native-marker"
    body = (
        f"import pathlib,sys\nsys.stdin.read()\npathlib.Path({str(marker)!r}).touch()\n"
    )
    local = fake(repository, body)
    selected: Path | str = local
    if kind == "path_name":
        monkeypatch.setenv("PATH", str(repository) + os.pathsep + os.environ["PATH"])
        selected = "fake-codex"
    elif kind == "external_alias":
        selected = tmp_path / "alias"
        selected.symlink_to(local)
    elif kind == "workspace_alias":
        local.unlink()
        outside = fake(tmp_path, body)
        local.symlink_to(outside)
    with pytest.raises(ValueError, match="executable.*repository"):
        invoke(repository, selected)
    assert not marker.exists()


def test_relative_external_native_keeps_repository_cwd_selection(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexTurnResult

    executable = fake(tmp_path, "import sys\nsys.stdin.read()\nprint('trusted')\n")
    result = invoke(repository, "../fake-codex")
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == "successful_exit"
    assert result.stdout.text == "trusted\n"
    assert result.argv[0] == str(executable.resolve())


def test_relative_selection_is_bound_at_turn_admission(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket
    from agent_harness.journal import EventJournal
    from agent_harness.turn import _validate_turn

    selected = tmp_path / "selected"
    replacement = tmp_path / "replacement"
    selected.mkdir()
    replacement.mkdir()
    trusted = fake(selected, "import sys\nsys.stdin.read()\nprint('selected')\n")
    other = fake(replacement, "import sys\nsys.stdin.read()\nprint('replacement')\n")
    alias = tmp_path / "alias"
    alias.symlink_to(trusted)
    adapter = CodexCLIAdapter("../alias")
    task = TaskContract("selection continuity", (), (), ("reply",))
    admission = record_admission(
        task, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = ContextPacket(str(repository), 1000, ())
    budgets = TurnBudgets(3, 10000, 100, 100, 100)
    assert (
        _validate_turn(
            adapter,
            task,
            admission,
            context,
            EventJournal(tmp_path / "state"),
            budgets,
            "workspace-write",
        )
        == repository
    )
    alias.unlink()
    alias.symlink_to(other)
    result = adapter.run(task, admission, context, budgets=budgets)
    assert result.process_status == "successful_exit"
    assert result.stdout.text == "selected\n"
    assert result.argv[0] == str(trusted.resolve())


@pytest.mark.parametrize("consumer", ["direct", "observed"])
def test_native_env_shebang_retry_ignores_planted_interpreter(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket
    from agent_harness.journal import EventJournal
    from agent_harness.turn import run_observed_turn

    tools = repository / "tools"
    tools.mkdir()
    external = tmp_path / "external-bin"
    external.mkdir()
    (external / "python3").symlink_to(sys.executable)
    trusted = tmp_path / "trusted-launches"
    planted = tmp_path / "outside-planted-marker"
    shadow = tools / "python3"
    payload = f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(planted)!r}).touch()\n"
    executable = fake(
        tmp_path,
        "import pathlib,sys\nsys.stdin.read()\n"
        f"p=pathlib.Path({str(trusted)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        f"s=pathlib.Path({str(shadow)!r}); s.write_text({payload!r}); s.chmod(0o700)\n"
        "sys.exit(1)\n",
    )
    executable.write_text(
        executable.read_text().replace(
            f"#!{sys.executable}", "#!/usr/bin/env python3", 1
        )
    )
    monkeypatch.setenv("PATH", os.pathsep.join((str(tools), str(external), os.defpath)))
    adapter = CodexCLIAdapter(executable)
    task = TaskContract("retry trusted launcher", (), (), ("reply",))
    admission = record_admission(
        task, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = ContextPacket(str(repository), 1000, ())
    budgets = TurnBudgets(3, 10000, 100, 100, 100)
    journal = EventJournal(tmp_path / "state")
    results = []
    for _ in range(2):
        if consumer == "observed":
            result = run_observed_turn(
                adapter, task, admission, context, journal=journal, budgets=budgets
            ).adapter_result
        else:
            result = adapter.run(task, admission, context, budgets=budgets)
        results.append(result)
    assert shadow.is_file()
    assert not planted.exists()
    assert trusted.read_text() == "xx"
    assert all(
        result.argv[0] == str(executable.resolve())
        and result.process_status == "nonzero_exit"
        and result.exit_code == 1
        for result in results
    )


@pytest.mark.parametrize(
    "path_alias", [False, True], ids=["physical", "controlled-alias"]
)
@pytest.mark.parametrize("shebang", ["absolute", "env", "env-S", "virtualenv"])
def test_native_shebang_preserves_external_runtime_and_caller_environment(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shebang: str,
    path_alias: bool,
) -> None:
    import venv

    from agent_harness.adapter import CodexTurnResult

    external = tmp_path / "external-bin"
    external.mkdir()
    (external / "python3").symlink_to(sys.executable)
    pythonpath = tmp_path / "external-modules"
    pythonpath.mkdir()
    search = external
    additional: tuple[str, ...] = ()
    if path_alias:
        search = tmp_path / "external-alias"
        search.symlink_to(external, target_is_directory=True)
        second = tmp_path / "second-external"
        second.mkdir()
        additional = (str(second.resolve()),)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(repository), str(search), *additional, os.defpath))
    )
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((str(repository), str(pythonpath)))
    )
    monkeypatch.setenv("MERCURY_CALLER", "configuration/authentication remains active")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "caller-config"))
    interpreter = sys.executable
    # An external alias without pyvenv.cfg starts the base runtime. The explicit
    # external virtualenv below must preserve its own interpreter path instead.
    prefix = sys.base_prefix if shebang in {"env", "env-S"} else sys.prefix
    if shebang == "virtualenv":
        virtualenv = tmp_path / "external-venv"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(virtualenv)
        interpreter = str(virtualenv / "bin" / "python")
        prefix = str(virtualenv)
    declaration = {
        "env": "/usr/bin/env python3",
        "env-S": "/usr/bin/env -S python3 -u",
    }.get(shebang, interpreter)
    executable = fake(
        tmp_path,
        "import json,os,sys\nsys.stdin.read()\n"
        "print(json.dumps({'prefix':sys.prefix,'path':os.environ['PATH'],"
        "'pythonpath':os.environ['PYTHONPATH'],'caller':os.environ['MERCURY_CALLER'],"
        "'config':os.environ['CODEX_HOME']}))\n",
    )
    executable.write_text(
        executable.read_text().replace(f"#!{sys.executable}", f"#!{declaration}", 1)
    )
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket

    task = TaskContract("preserve external native runtime", (), (), ("reply",))
    result = CodexCLIAdapter(executable).run(
        task,
        record_admission(task, approver="human", approved_at="2026-10-01T00:00:00Z"),
        ContextPacket(str(repository), 1000, ()),
        budgets=TurnBudgets(3, 10000, 4000, 1000, 100),
    )
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == "successful_exit"
    returned = json.loads(result.stdout.text)
    assert returned["prefix"] == prefix
    expected_directories = (
        str(external.resolve()),
        *additional,
        *(str(Path(entry).resolve()) for entry in os.defpath.split(os.pathsep)),
    )
    assert returned["path"].split(os.pathsep) == list(expected_directories)
    assert str(repository) not in returned["path"].split(os.pathsep)
    if path_alias:
        assert str(search) not in returned["path"].split(os.pathsep)
    assert returned["pythonpath"] == str(pythonpath)
    assert returned["caller"] == "configuration/authentication remains active"
    assert returned["config"] == str(tmp_path / "caller-config")
    assert result.argv[0] == str(executable.resolve())


@pytest.mark.parametrize(
    "kind", ["repository_interpreter", "env_quoted", "env_nonascii"]
)
def test_native_unsafe_shebang_is_refused_before_launch(
    repository: Path, tmp_path: Path, kind: str
) -> None:
    marker = tmp_path / "outside-marker"
    interpreter = fake(
        repository, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    declaration = {
        "repository_interpreter": str(interpreter),
        "env_quoted": '/usr/bin/env -S "python3"',
        "env_nonascii": "/usr/bin/env\u00a0python3",
    }[kind]
    executable = fake(
        tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    executable.write_text(
        executable.read_text().replace(f"#!{sys.executable}", f"#!{declaration}", 1)
    )
    with pytest.raises(ValueError, match="outside|ambiguous"):
        invoke(repository, executable)
    assert not marker.exists()


def test_native_retry_rechecks_changed_interpreter_boundary(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket

    marker = tmp_path / "outside-marker"
    executable = fake(tmp_path, "import sys\nsys.stdin.read()\nsys.exit(1)\n")
    adapter = CodexCLIAdapter(executable)
    task = TaskContract("recheck native launch", (), (), ("reply",))
    admission = record_admission(
        task, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = ContextPacket(str(repository), 1000, ())
    budgets = TurnBudgets(3, 10000, 100, 100, 100)
    assert (
        adapter.run(task, admission, context, budgets=budgets).process_status
        == "nonzero_exit"
    )
    interpreter = fake(
        repository, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    executable.write_text(f"#!{interpreter}\n")
    with pytest.raises(ValueError, match="outside"):
        adapter.run(task, admission, context, budgets=budgets)
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["absolute", "env"])
def test_missing_native_interpreter_remains_typed_launch_error(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from agent_harness.adapter import CodexTurnResult

    monkeypatch.setenv("PATH", os.defpath)
    declaration = (
        str(tmp_path / "missing-interpreter")
        if kind == "absolute"
        else "/usr/bin/env mercury-missing-interpreter"
    )
    executable = fake(tmp_path, "raise AssertionError('must not launch')\n")
    executable.write_text(
        f"#!{declaration}\n" + executable.read_text().split("\n", 1)[1]
    )
    result = invoke(repository, executable)
    assert isinstance(result, CodexTurnResult)
    assert result.process_status == "launch_error"
    assert result.exit_code is None
    assert result.stderr.retained_bytes <= 37
    assert result.argv[0] == str(executable.resolve())


@pytest.mark.parametrize("kind", ["entry", "interpreter"])
def test_native_hardlink_mutation_is_refused_on_retry(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.context import ContextPacket

    ignored = repository / "ignored"
    ignored.mkdir()
    (repository / ".gitignore").write_text("ignored/\n")
    alias = ignored / "native-alias"
    marker = tmp_path / "outside-retry-marker"
    launches = tmp_path / "trusted-launches"
    replacement = (
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    external = fake(
        tmp_path,
        "import pathlib,sys\nsys.stdin.read()\n"
        f"pathlib.Path({str(launches)!r}).write_text('first')\n"
        f"pathlib.Path({str(alias)!r}).write_text({replacement!r})\n"
        "sys.exit(1)\n",
    )
    selected = external
    if kind == "interpreter":
        selected = tmp_path / "launcher"
        selected.write_text(f"#!/usr/bin/env {external.name}\n")
        selected.chmod(0o700)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.defpath)
    alias.hardlink_to(external)
    original = external.read_bytes()
    task = TaskContract("retry with ignored alias", ("ignored",), (), ("reply",))
    admission = record_admission(
        task, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    adapter = CodexCLIAdapter(selected)
    context = ContextPacket(str(repository), 1000, ())
    budgets = TurnBudgets(3, 10000, 100, 100, 100)
    first = adapter.run(task, admission, context, budgets=budgets)
    assert first.argv[0] == str(selected.resolve())
    assert first.process_status == "nonzero_exit" and first.exit_code == 1
    assert launches.read_text() == "first"
    assert external.read_bytes() != original
    assert alias.stat().st_ino == external.stat().st_ino
    retry = adapter.run(task, admission, context, budgets=budgets)
    assert retry.argv[0] == first.argv[0]
    assert not marker.exists(), (
        "changed hard-linked bytes executed before retry sandbox"
    )
    assert retry.process_status == "launch_error" and retry.exit_code is None
    assert "changed" in retry.stderr.text
    assert not marker.exists()

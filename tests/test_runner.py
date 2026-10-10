from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_harness.runner as runner_module
from agent_harness.policy import VerificationPolicy
from agent_harness.runner import ControlledCommandRunner


def test_runner_captures_successful_command_evidence(tmp_path: Path) -> None:
    command = (sys.executable, "-c", "print('standard output')")
    policy = VerificationPolicy((command,), ".", 5)

    result = ControlledCommandRunner().run(policy, tmp_path)[0]

    assert result.command == command
    assert result.exit_code == 0
    assert result.stdout == "standard output\n"
    assert result.stderr == ""
    assert result.duration_seconds >= 0
    assert not result.timed_out


def test_runner_collects_nonzero_exit_without_stopping_later_commands(
    tmp_path: Path,
) -> None:
    failing = (
        sys.executable,
        "-c",
        "import sys; print('failure output'); print('failure error', file=sys.stderr); raise SystemExit(7)",
    )
    succeeding = (sys.executable, "-c", "print('later command')")
    policy = VerificationPolicy((failing, succeeding), ".", 5)

    results = ControlledCommandRunner().run(policy, tmp_path)

    assert [result.exit_code for result in results] == [7, 0]
    assert results[0].stdout == "failure output\n"
    assert results[0].stderr == "failure error\n"
    assert results[1].stdout == "later command\n"


def test_runner_records_timeout_as_result_evidence(tmp_path: Path) -> None:
    policy = VerificationPolicy(
        ((sys.executable, "-c", "import time; time.sleep(2)"),), ".", 1
    )

    result = ControlledCommandRunner().run(policy, tmp_path)[0]

    assert result.exit_code is None
    assert result.timed_out
    assert result.duration_seconds >= 1


def test_runner_preserves_arbitrary_output_bytes_and_continues_batch(
    tmp_path: Path,
) -> None:
    stdout = b"stdout\xff\x00\r\n"
    stderr = b"stderr\xfe\r\n"
    command = (
        sys.executable,
        "-c",
        f"import sys; sys.stdout.buffer.write({stdout!r}); "
        f"sys.stderr.buffer.write({stderr!r})",
    )
    later = (sys.executable, "-c", "print('later command')")
    policy = VerificationPolicy((command, later), ".", 5)

    results = ControlledCommandRunner().run(policy, tmp_path)

    assert len(results) == 2
    assert results[0].exit_code == 0
    assert not results[0].timed_out
    assert results[0].stdout.encode("utf-8", errors="surrogateescape") == stdout
    assert results[0].stderr.encode("utf-8", errors="surrogateescape") == stderr
    assert results[1].stdout == "later command\n"


def test_runner_records_command_launch_failure(tmp_path: Path) -> None:
    policy = VerificationPolicy((("definitely-not-an-installed-command",),), ".", 5)

    result = ControlledCommandRunner().run(policy, tmp_path)[0]

    assert result.exit_code is None
    assert result.stderr
    assert not result.timed_out


def test_runner_preserves_timeout_partial_bytes_and_continues_batch(
    tmp_path: Path,
) -> None:
    stdout = b"partial\xff\r\n"
    stderr = b"partial error\xfe\x00"
    command = (
        sys.executable,
        "-c",
        f"import os, time; os.write(1, {stdout!r}); "
        f"os.write(2, {stderr!r}); time.sleep(10)",
    )
    later = (sys.executable, "-c", "print('after timeout')")
    policy = VerificationPolicy((command, later), ".", 1)

    results = ControlledCommandRunner().run(policy, tmp_path)

    assert len(results) == 2
    assert results[0].timed_out
    assert results[0].exit_code is None
    assert results[0].stdout.encode("utf-8", errors="surrogateescape") == stdout
    assert results[0].stderr.encode("utf-8", errors="surrogateescape") == stderr
    assert results[1].stdout == "after timeout\n"


def test_runner_rejects_symlinked_working_directory_outside_repository(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / "escape").symlink_to(outside, target_is_directory=True)
    policy = VerificationPolicy(
        ((sys.executable, "-c", "print('unsafe')"),), "escape", 5
    )

    with pytest.raises(ValueError, match="inside the repository"):
        ControlledCommandRunner().run(policy, repository)


def _process_is_running(pid: int) -> bool:
    # Orphan zombies on POSIX are stopped even before their new parent reaps them.
    completed = subprocess.run(
        ("ps", "-o", "stat=", "-p", str(pid)),
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    state = completed.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _cleanup_fixture_processes(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.exists():
            try:
                os.kill(int(path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_timeout_stops_real_grandchild_and_preserves_unrelated_process(
    tmp_path: Path,
) -> None:
    parent_pid = tmp_path / "parent.pid"
    child_pid = tmp_path / "child.pid"
    grandchild_pid = tmp_path / "grandchild.pid"
    marker = tmp_path / "delayed-marker"
    grandchild_script = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(grandchild_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(3); "
        f"pathlib.Path({str(marker)!r}).write_text('leaked'); time.sleep(30)"
    )
    child_script = (
        "import os, pathlib, subprocess, sys, time; "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        f"subprocess.Popen((sys.executable, '-c', {grandchild_script!r}), "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(30)"
    )
    parent_script = (
        "import os, pathlib, subprocess, sys, time; "
        f"pathlib.Path({str(parent_pid)!r}).write_text(str(os.getpid())); "
        f"subprocess.Popen((sys.executable, '-c', {child_script!r}), "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(30)"
    )
    policy = VerificationPolicy(((sys.executable, "-c", parent_script),), ".", 1)
    unrelated = subprocess.Popen((sys.executable, "-c", "import time; time.sleep(30)"))
    try:
        started = time.monotonic()
        result = ControlledCommandRunner().run(policy, tmp_path)[0]

        assert result.timed_out
        assert result.exit_code is None
        assert time.monotonic() - started < 4
        assert grandchild_pid.exists(), "fixture must create the real grandchild"
        deadline = time.monotonic() + 1
        while _process_is_running(int(grandchild_pid.read_text())):
            if time.monotonic() >= deadline:
                pytest.fail("grandchild remains running after command timeout")
            time.sleep(0.02)
        assert not _process_is_running(int(child_pid.read_text()))
        assert not _process_is_running(int(parent_pid.read_text()))
        time.sleep(2.1)
        assert not marker.exists()
        assert unrelated.poll() is None
    finally:
        _cleanup_fixture_processes((grandchild_pid, child_pid, parent_pid))
        unrelated.kill()
        unrelated.wait(timeout=2)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_runner_isolates_each_command_session_and_process_group(tmp_path: Path) -> None:
    command = (
        sys.executable,
        "-c",
        "import os; print(os.getpid(), os.getpgrp(), os.getsid(0))",
    )
    policy = VerificationPolicy((command, command), ".", 5)

    results = ControlledCommandRunner().run(policy, tmp_path)

    for result in results:
        pid, process_group, session = map(int, result.stdout.split())
        assert result.exit_code == 0
        assert process_group == session == pid
        assert process_group != os.getpgrp()
        assert session != os.getsid(0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
@pytest.mark.parametrize("parent_exits_first", [False, True])
def test_timeout_cleans_inherited_pipes_reaps_child_and_preserves_bytes(
    tmp_path: Path, parent_exits_first: bool
) -> None:
    parent_pid = tmp_path / "parent.pid"
    child_pid = tmp_path / "child.pid"
    stdout = b"parent\xff\x00\r\n" + b"child\xfe" * 40000
    stderr = b"error\xfd\r\n" * 30000
    child_script = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "os.write(1, b'child\\xfe' * 40000); "
        "os.write(2, b'error\\xfd\\r\\n' * 30000); time.sleep(30)"
    )
    parent_script = (
        "import os, pathlib, subprocess, sys, time; "
        f"pathlib.Path({str(parent_pid)!r}).write_text(str(os.getpid())); "
        "os.write(1, b'parent\\xff\\x00\\r\\n'); "
        f"subprocess.Popen((sys.executable, '-c', {child_script!r})); "
        + ("sys.exit(0)" if parent_exits_first else "time.sleep(30)")
    )
    later = (sys.executable, "-c", "print('after descendant cleanup')")
    policy = VerificationPolicy(((sys.executable, "-c", parent_script), later), ".", 1)
    try:
        started = time.monotonic()
        results = ControlledCommandRunner().run(policy, tmp_path)

        assert time.monotonic() - started < 4
        assert results[0].timed_out
        assert results[0].exit_code is None
        assert results[0].stdout.encode("utf-8", errors="surrogateescape") == stdout
        assert results[0].stderr.encode("utf-8", errors="surrogateescape") == stderr
        assert child_pid.exists(), "fixture must create the pipe-retaining child"
        assert not _process_is_running(int(child_pid.read_text()))
        with pytest.raises(ChildProcessError):
            os.waitpid(int(parent_pid.read_text()), os.WNOHANG)
        assert results[1].exit_code == 0
        assert results[1].stdout == "after descendant cleanup\n"
    finally:
        _cleanup_fixture_processes((child_pid, parent_pid))


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
@pytest.mark.parametrize("parent_exits_first", [False, True])
@pytest.mark.parametrize("failure_kind", ["overflow", "interrupt", "read-error"])
def test_collection_exception_cleans_real_group_and_preserves_original_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_exits_first: bool,
    failure_kind: str,
) -> None:
    child_pid = tmp_path / "child.pid"
    ready = tmp_path / "ready"
    child_script = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "os.write(1, b'child output'); os.write(2, b'child error'); "
        f"pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(30)"
    )
    parent_script = (
        "import pathlib, subprocess, sys, time; "
        f"subprocess.Popen((sys.executable, '-c', {child_script!r})); "
        f"ready = pathlib.Path({str(ready)!r}); "
        "deadline = time.monotonic() + 5\n"
        "while not ready.exists():\n"
        "    if time.monotonic() >= deadline: raise RuntimeError('child not ready')\n"
        "    time.sleep(0.01)\n"
        + ("sys.exit(0)" if parent_exits_first else "time.sleep(30)")
    )
    # Huge positive integers remain valid policy inputs; CPython overflows later.
    policy = VerificationPolicy(((sys.executable, "-c", parent_script),), ".", 10**1000)
    launch = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []
    errors: list[BaseException] = []
    unrelated = launch((sys.executable, "-c", "import time; time.sleep(30)"))

    def launch_ready_process(
        command: tuple[str, ...],
        *,
        cwd: Path,
        stdout: int,
        stderr: int,
        start_new_session: bool,
        env: dict[str, str],
    ) -> subprocess.Popen[bytes]:
        process = launch(
            command,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            start_new_session=start_new_session,
            env=env,
        )
        processes.append(process)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() >= deadline:
                pytest.fail("real process group did not become ready")
            time.sleep(0.01)
        if parent_exits_first:
            # Observe leader exit without reaping it on behalf of the runner.
            with monkeypatch.context() as launch_context:
                launch_context.setattr(subprocess, "Popen", launch)
                while _process_is_running(process.pid):
                    if time.monotonic() >= deadline:
                        pytest.fail("real process leader did not exit")
                    time.sleep(0.01)
        return process

    collect = runner_module._collect_output

    def record_real_collect(
        process: subprocess.Popen[bytes],
        stdout: runner_module._OutputBuffer,
        stderr: runner_module._OutputBuffer,
        timeout: int,
    ) -> None:
        if failure_kind != "overflow" and not errors:
            failure = (
                KeyboardInterrupt("interrupted")
                if failure_kind == "interrupt"
                else OSError("read failed")
            )
            errors.append(failure)
            raise failure
        try:
            collect(process, stdout, stderr, timeout)
        except OverflowError as error:
            errors.append(error)
            raise

    monkeypatch.setattr(runner_module, "_collect_output", record_real_collect)
    monkeypatch.setattr(subprocess, "Popen", launch_ready_process)
    try:
        started = time.monotonic()
        failure_type = {
            "overflow": OverflowError,
            "interrupt": KeyboardInterrupt,
            "read-error": OSError,
        }[failure_kind]
        with pytest.raises(failure_type) as raised:
            ControlledCommandRunner().run(policy, tmp_path)

        monkeypatch.setattr(subprocess, "Popen", launch)
        assert raised.value is errors[0]
        assert time.monotonic() - started < 4
        assert not _process_is_running(int(child_pid.read_text()))
        with pytest.raises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
        assert unrelated.poll() is None
    finally:
        # Restore Popen before ps inspection and always reap fixture-owned children.
        monkeypatch.setattr(subprocess, "Popen", launch)
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        _cleanup_fixture_processes((child_pid,))
        unrelated.kill()
        unrelated.wait(timeout=2)


@pytest.mark.parametrize("size", [4095, 4096, 4097, 2000000])
def test_runner_bounds_each_stream_and_marks_only_excess(
    tmp_path: Path, size: int
) -> None:
    cap = 4096
    command = (
        sys.executable,
        "-c",
        f"import os; os.write(1, b'a' * {size}); os.write(2, b'b' * {size})",
    )
    result = ControlledCommandRunner(max_output_bytes=cap).run(
        VerificationPolicy((command,), ".", 5), tmp_path
    )[0]
    assert result.exit_code == 0
    assert not result.timed_out
    for text, byte in ((result.stdout, b"a"), (result.stderr, b"b")):
        output = text.encode("utf-8", errors="surrogateescape")
        assert len(output) <= cap
        if size <= cap:
            assert output == byte * size
        else:
            assert output.startswith(byte * 100)
            assert b"[output truncated]" in output


def test_runner_default_budget_is_one_mib_per_stream(tmp_path: Path) -> None:
    command = (
        sys.executable,
        "-c",
        "import os; os.write(1, b'a' * 2000000); os.write(2, b'b' * 2000000)",
    )
    result = ControlledCommandRunner().run(
        VerificationPolicy((command,), ".", 5), tmp_path
    )[0]
    assert result.exit_code == 0
    for output in (result.stdout, result.stderr):
        assert len(output.encode()) <= 1024 * 1024
        assert "[output truncated]" in output


def test_runner_drains_continuous_output_until_timeout_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_communicate(*args: object, **kwargs: object) -> None:
        pytest.fail("runner must never buffer output through communicate")

    monkeypatch.setattr(subprocess.Popen, "communicate", no_communicate)
    read = os.read
    sizes: list[int] = []

    def bounded_read(fd: int, size: int) -> bytes:
        assert 0 < size <= 65536
        sizes.append(size)
        return read(fd, size)

    monkeypatch.setattr(os, "read", bounded_read)
    command = (
        sys.executable,
        "-c",
        "import os\nwhile True: os.write(1, b'a' * 8192); os.write(2, b'b' * 8192)",
    )
    later = (sys.executable, "-c", "print('after noise')")
    started = time.monotonic()
    results = ControlledCommandRunner(max_output_bytes=4096).run(
        VerificationPolicy((command, later), ".", 1), tmp_path
    )
    assert time.monotonic() - started < 4
    assert results[0].timed_out
    assert results[0].exit_code is None
    for output in (results[0].stdout, results[0].stderr):
        assert len(output.encode()) <= 4096
        assert "[output truncated]" in output
    assert results[1].stdout == "after noise\n"
    assert sizes


@pytest.mark.parametrize("cap", [True, False, 0, -1, 1.5, "4096", 1])
def test_runner_rejects_invalid_output_budgets(cap: object) -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        ControlledCommandRunner(max_output_bytes=cap)  # type: ignore[arg-type]


def test_verification_ignores_foreign_git_selectors_and_preserves_command_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "target"
    foreign = tmp_path / "foreign"
    for root in (repository, foreign):
        subprocess.run(("git", "init", "--quiet", str(root)), check=True)
        (root / "tracked.txt").write_text("original")
        subprocess.run(("git", "-C", str(root), "add", "."), check=True)
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ),
            check=True,
        )
    (repository / "tracked.txt").write_text("dirty")
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("MERCURY_VERIFICATION_FIXTURE", "retained")
    policy = VerificationPolicy(
        (
            ("git", "diff", "--quiet"),
            (
                sys.executable,
                "-c",
                "import os; print(os.environ['MERCURY_VERIFICATION_FIXTURE'])",
            ),
        ),
        ".",
        5,
    )
    results = ControlledCommandRunner().run(policy, repository)
    assert results[0].exit_code == 1
    assert not results[0].timed_out
    assert results[1].exit_code == 0
    assert results[1].stdout == "retained\n"

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
        command: tuple[str, ...] | list[str], **kwargs: Any
    ) -> subprocess.Popen[bytes]:
        if tuple(command) != policy.commands[0]:
            # New read-only metadata preflight is not the readiness subject.
            return launch(command, **kwargs)
        process = launch(command, **kwargs)
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

    read = os.read
    sizes: list[int] = []

    def bounded_read(fd: int, size: int) -> bytes:
        assert 0 < size <= 65536
        sizes.append(size)
        return read(fd, size)

    command = (
        sys.executable,
        "-c",
        "import os\nwhile True: os.write(1, b'a' * 8192); os.write(2, b'b' * 8192)",
    )
    later = (sys.executable, "-c", "print('after noise')")
    policy = VerificationPolicy((command, later), ".", 1)
    selections = runner_module.bind_verification_executables(policy, tmp_path)
    monkeypatch.setattr(subprocess.Popen, "communicate", no_communicate)
    monkeypatch.setattr(os, "read", bounded_read)
    with pytest.raises(pytest.fail.Exception, match="never buffer"):
        subprocess.Popen.communicate(object())  # type: ignore[arg-type]
    started = time.monotonic()
    results = ControlledCommandRunner(max_output_bytes=4096).run(
        policy, tmp_path, selections=selections
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


@pytest.mark.parametrize(
    "location",
    ["direct", "lexical_alias", "resolved_alias", "git_metadata", "separate_metadata"],
)
def test_verification_selection_rejects_repository_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    init_command: tuple[str, ...] = ("git", "init", "--quiet", str(repository))
    if location == "separate_metadata":
        init_command = (
            "git",
            "init",
            "--quiet",
            "--separate-git-dir",
            str(tmp_path / "metadata"),
            str(repository),
        )
    subprocess.run(init_command, check=True, timeout=10)
    external = tmp_path / "checker"
    external.write_text("#!/bin/sh\nexit 0\n")
    external.chmod(0o700)
    internal = repository / "checker"
    if location == "git_metadata":
        internal = repository / ".git" / "checker"
    if location == "separate_metadata":
        internal = tmp_path / "metadata" / "checker"
    if location == "lexical_alias":
        internal.symlink_to(external)
        selected = internal
    else:
        internal.write_text("#!/bin/sh\nexit 0\n")
        internal.chmod(0o700)
        if location == "resolved_alias":
            external.unlink()
            external.symlink_to(internal)
            selected = external
        else:
            selected = internal
    with pytest.raises(ValueError, match="outside"):
        ControlledCommandRunner().run(
            VerificationPolicy(((str(selected),),), ".", 2), repository
        )


def test_bound_verification_preserves_selection_and_missing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / "repository"
    root.mkdir()
    checker = external / "checker"
    checker.write_text("#!/bin/sh\n/usr/bin/printenv MERCURY_SELECTION\n")
    checker.chmod(0o700)
    monkeypatch.setenv("PATH", str(external) + os.pathsep + os.defpath)
    monkeypatch.setenv("MERCURY_SELECTION", "retained")
    policy = VerificationPolicy((("checker",), ("missing-checker",)), ".", 2)
    selected = runner_module.bind_verification_executables(policy, root)
    baseline = ControlledCommandRunner().run(policy, root, selections=selected)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    for name in ("checker", "missing-checker"):
        program = replacement / name
        program.write_text("#!/bin/sh\necho fabricated\n")
        program.chmod(0o700)
    monkeypatch.setenv("PATH", str(replacement))
    later = ControlledCommandRunner().run(policy, root, selections=selected)
    assert baseline[0].stdout == later[0].stdout == "retained\n"
    assert later[0].command == ("checker",)
    assert baseline[1].exit_code is later[1].exit_code is None
    assert later[1].stderr and not later[1].timed_out


@pytest.mark.parametrize("invocation", ["absolute", "path_name"])
def test_bound_selection_preserves_external_virtualenv_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    venv = tmp_path / "external-venv"
    subprocess.run(
        (sys.executable, "-m", "venv", "--without-pip", str(venv)),
        check=True,
        timeout=30,
    )
    python = venv / "bin" / "python"
    monkeypatch.setenv("PATH", str(venv / "bin"))
    command = (
        str(python) if invocation == "absolute" else "python",
        "-c",
        "import sys; print(sys.prefix)",
    )
    policy = VerificationPolicy((command,), ".", 5)
    selected = runner_module.bind_verification_executables(policy, root)
    monkeypatch.setenv("PATH", os.defpath)
    result = ControlledCommandRunner().run(policy, root, selections=selected)[0]
    assert result.exit_code == 0
    assert result.stdout.strip() == str(venv)
    assert result.command == command


@pytest.mark.parametrize("invocation", ["relative_path", "relative_path_entry"])
def test_verification_relative_selection_uses_policy_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    root = tmp_path / "repository"
    (root / "sub").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    checker = external / "checker"
    checker.write_text("#!/bin/sh\npwd\n")
    checker.chmod(0o700)
    monkeypatch.setenv("PATH", "../../external")
    command = (
        "../../external/checker" if invocation == "relative_path" else "checker",
    )
    result = ControlledCommandRunner().run(
        VerificationPolicy((command,), "sub", 2), root
    )[0]
    assert result.exit_code == 0
    assert result.stdout.strip() == str(root / "sub")
    assert result.command == command


def test_verification_binds_entire_batch_before_any_launch(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    marker = tmp_path / "first-launch"
    internal = root / "checker"
    internal.write_text("#!/bin/sh\nexit 0\n")
    internal.chmod(0o700)
    first = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    )
    policy = VerificationPolicy((first, (str(internal),)), ".", 2)
    with pytest.raises(ValueError, match="outside"):
        ControlledCommandRunner().run(policy, root)
    assert not marker.exists()


def test_verification_rejects_repository_in_intermediate_alias_chain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    external = tmp_path / "external-checker"
    external.write_text("#!/bin/sh\nexit 0\n")
    external.chmod(0o700)
    internal_alias = root / "mutable-alias"
    internal_alias.symlink_to(external)
    selected = tmp_path / "selected-alias"
    selected.symlink_to(internal_alias)
    assert selected.resolve() == external
    with pytest.raises(ValueError, match="outside"):
        ControlledCommandRunner().run(
            VerificationPolicy(((str(selected),),), ".", 2), root
        )


def test_bound_nonregular_selection_stays_typed_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    selected = tmp_path / "external-fifo"
    os.mkfifo(selected)
    policy = VerificationPolicy(((str(selected),),), ".", 2)
    bindings = runner_module.bind_verification_executables(policy, root)
    selected.unlink()
    selected.write_text("#!/bin/sh\nexit 0\n")
    selected.chmod(0o700)
    result = ControlledCommandRunner().run(policy, root, selections=bindings)[0]
    assert result.exit_code is None and not result.timed_out
    assert "regular file" in result.stderr


@pytest.mark.parametrize("invocation", ["direct", "path_entry"])
def test_verification_keeps_symlink_dotdot_lookup_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    lexical = tmp_path / "lexical"
    lexical.mkdir()
    actual = tmp_path / "actual"
    (actual / "child").mkdir(parents=True)
    (lexical / "alias").symlink_to(actual / "child", target_is_directory=True)
    for directory, text in ((lexical, "wrong"), (actual, "selected")):
        program = directory / "checker"
        program.write_text(f"#!/bin/sh\necho {text}\n")
        program.chmod(0o700)
    spelling = lexical / "alias" / ".." / "checker"
    monkeypatch.setenv("PATH", str(spelling.parent))
    command = (str(spelling) if invocation == "direct" else "checker",)
    result = ControlledCommandRunner().run(
        VerificationPolicy((command,), ".", 2), root
    )[0]
    assert result.stdout == "selected\n"
    assert result.command == command


@pytest.mark.parametrize(
    "location", ["worktree", "actual_metadata", "common_metadata", "intermediate_alias"]
)
def test_verification_rejects_physical_case_alias(
    tmp_path: Path, location: str
) -> None:
    root = tmp_path / "repository"
    metadata = tmp_path / "metadata"
    subprocess.run(
        ("git", "init", "--quiet", "--separate-git-dir", str(metadata), str(root)),
        check=True,
        timeout=10,
    )
    boundary = root if location in ("worktree", "intermediate_alias") else metadata
    if location == "common_metadata":
        linked = tmp_path / "linked-worktree"
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ),
            check=True,
            timeout=10,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(linked),
            ),
            check=True,
            timeout=10,
        )
        root = linked
    alias = boundary.with_name(boundary.name.upper())
    if not alias.exists() or not alias.samefile(boundary):
        pytest.skip("filesystem does not expose the actual case-alias premise")
    assert alias.samefile(boundary)
    marker = tmp_path / "repository-launch"
    program = boundary / "checker"
    program.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    program.chmod(0o700)
    selected = alias / "checker"
    if location == "intermediate_alias":
        external = tmp_path / "external-checker"
        external.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        external.chmod(0o700)
        program.unlink()
        program.symlink_to(external)
        selected = tmp_path / "selected-alias"
        selected.symlink_to(alias / "checker")
        assert selected.resolve() == external
    first_marker = tmp_path / "first-launch"
    first = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(first_marker)!r}).touch()",
    )
    with pytest.raises(ValueError, match="outside"):
        ControlledCommandRunner().run(
            VerificationPolicy((first, (str(selected),)), ".", 2), root
        )
    assert not first_marker.exists()
    assert not marker.exists()


@pytest.mark.parametrize("location", ["distinct_directory", "case_sensitive_sibling"])
def test_verification_accepts_external_distinct_identity(
    tmp_path: Path, location: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    external = tmp_path / (
        "REPOSITORY" if location == "case_sensitive_sibling" else "external"
    )
    external.mkdir(exist_ok=True)
    if external.samefile(root):
        pytest.skip("filesystem cannot create a distinct case-sensitive sibling")
    assert not external.samefile(root)
    program = external / "checker"
    program.write_text("#!/bin/sh\necho external\n")
    program.chmod(0o700)
    command = (str(program),)
    policy = VerificationPolicy((command,), ".", 2)
    bindings = runner_module.bind_verification_executables(policy, root)
    result = ControlledCommandRunner().run(policy, root, selections=bindings)[0]
    assert result.exit_code == 0
    assert result.stdout == "external\n"
    assert result.command == command


def test_indirect_verification_uses_frozen_external_resolvers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    interpreter = external / "generic-runtime"
    interpreter.symlink_to("/bin/sh")
    helper = external / "generic-helper"
    helper.write_text("#!/bin/sh\necho trusted\n")
    helper.chmod(0o700)
    checker = external / "checker"
    checker.write_text("#!/usr/bin/env generic-runtime\ngeneric-helper\n")
    checker.chmod(0o700)
    monkeypatch.setenv(
        "PATH", str(root) + os.pathsep + str(external) + os.pathsep + os.defpath
    )
    policy = VerificationPolicy(((str(checker),),), ".", 2)
    bindings = runner_module.bind_verification_executables(policy, root)
    for name in ("generic-runtime", "generic-helper"):
        planted = root / name
        planted.write_text("#!/bin/sh\necho fabricated\n")
        planted.chmod(0o700)
    monkeypatch.setenv("PATH", str(root))
    result = ControlledCommandRunner().run(policy, root, selections=bindings)[0]
    assert result.exit_code == 0
    assert result.stdout == "trusted\n"


@pytest.mark.parametrize("entry", ["", ".", "alias", ".git", "case_alias"])
def test_child_search_excludes_mutable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(("git", "init", "--quiet", str(root)), check=True, timeout=10)
    (root / "alias").symlink_to(root, target_is_directory=True)
    value = str(root).upper() if entry == "case_alias" else entry
    if entry == "case_alias":
        alias = Path(value)
        if not alias.exists() or not alias.samefile(root):
            pytest.skip("filesystem does not expose this physical case alias")
        assert alias.samefile(root)
    monkeypatch.setenv("PATH", value)
    command = (sys.executable, "-c", "import os; print(os.environ['PATH'])")
    policy = VerificationPolicy((command,), ".", 2)
    binding = runner_module.bind_verification_executables(policy, root)
    result = ControlledCommandRunner().run(policy, root, selections=binding)[0]
    assert result.exit_code == 0
    assert result.stdout.strip() == "/dev/null/mercury-no-search"
    assert os.environ["PATH"] == value


@pytest.mark.parametrize("alias", [False, True])
def test_rejects_repository_shebang_interpreter(tmp_path: Path, alias: bool) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    interpreter = root / "runtime"
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o700)
    if alias:
        link = tmp_path / "runtime-alias"
        link.symlink_to(interpreter)
        interpreter = link
    checker = tmp_path / "checker"
    checker.write_text(f"#!{interpreter}\nexit 0\n")
    checker.chmod(0o700)
    with pytest.raises(ValueError, match="outside"):
        runner_module.bind_verification_executables(
            VerificationPolicy(((str(checker),),), ".", 2), root
        )


def test_loader_resolvers_preserve_safe_caller_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(("git", "init", "--quiet", str(root)), check=True, timeout=10)
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(root) + os.pathsep + str(external))
    monkeypatch.setenv("MERCURY_CALLER", "retained")
    command = (
        sys.executable,
        "-c",
        "import os; print(os.environ['PYTHONPATH']); print(os.environ['MERCURY_CALLER'])",
    )
    policy = VerificationPolicy((command,), ".", 2)
    binding = runner_module.bind_verification_executables(policy, root)
    monkeypatch.setenv("PYTHONPATH", str(root))
    result = ControlledCommandRunner().run(policy, root, selections=binding)[0]
    assert result.stdout == str(external) + "\nretained\n"
    monkeypatch.setenv("LD_PRELOAD", str(root / "planted-loader"))
    with pytest.raises(ValueError, match="outside"):
        runner_module.bind_verification_executables(policy, root)


@pytest.mark.parametrize("declaration", ["generic-runtime", "-S generic-runtime -e"])
def test_literal_env_split_shebang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (tmp_path / "generic-runtime").symlink_to("/bin/sh")
    checker = tmp_path / "checker"
    checker.write_text(f"#!/usr/bin/env {declaration}\necho literal\n")
    checker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    result = ControlledCommandRunner().run(
        VerificationPolicy(((str(checker),),), ".", 2), root
    )[0]
    assert result.exit_code == 0
    assert result.stdout == "literal\n"


@pytest.mark.parametrize("name", ["ENV", "BASH_ENV", "PYTHONSTARTUP"])
def test_literal_startup_path_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    startup = tmp_path / "startup file"
    startup.write_text("export MERCURY_STARTUP=trusted\n")
    monkeypatch.setenv(name, str(startup))
    command = (sys.executable, "-c", f"import os; print(os.environ[{name!r}])")
    policy = VerificationPolicy((command,), ".", 2)
    binding = runner_module.bind_verification_executables(policy, root)
    monkeypatch.setenv(name, str(root / "unsafe"))
    result = ControlledCommandRunner().run(policy, root, selections=binding)[0]
    assert result.stdout == str(startup) + "\n"
    with pytest.raises(ValueError, match="outside"):
        runner_module.bind_verification_executables(policy, root)


@pytest.mark.parametrize("name", ["ENV", "BASH_ENV"])
@pytest.mark.parametrize(
    "value", ["$HOME/startup", "$(echo unsafe)", "`pwd`/startup", "~/startup"]
)
def test_dynamic_startup_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="ambiguous"):
        runner_module.bind_verification_executables(
            VerificationPolicy(((sys.executable,),), ".", 2), tmp_path
        )


def test_interpreter_proof_uses_bounded_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    checker = tmp_path / "checker"
    checker.write_text("#!/bin/sh\nexit 0\n")
    checker.chmod(0o700)

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("whole-file read is not a finite streaming proof")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    binding = runner_module.bind_verification_executables(
        VerificationPolicy(((str(checker),),), ".", 2), root
    )
    assert binding.executables[0].launch_error is None
    assert binding.interpreter_proofs


@pytest.mark.parametrize(
    "kind", ["fifo", "directory", "missing", "cycle", "depth", "header"]
)
def test_interpreter_refusal_is_finite(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    checker = tmp_path / "checker"
    runtime = tmp_path / "runtime"
    checker.write_text(f"#!{runtime}\nexit 0\n")
    checker.chmod(0o700)
    if kind == "fifo":
        os.mkfifo(runtime)
    elif kind == "directory":
        runtime.mkdir()
    elif kind == "cycle":
        runtime.write_text(f"#!{checker}\n")
    elif kind == "depth":
        for index in range(12):
            path = runtime if index == 0 else tmp_path / f"runtime{index}"
            path.write_text(f"#!{tmp_path / f'runtime{index + 1}'}\n")
    elif kind == "header":
        runtime.write_text("#!/bin/sh " + "x" * 4096)
    script = (
        "from pathlib import Path; from agent_harness.runner import bind_verification_executables; "
        "from agent_harness.policy import VerificationPolicy; "
        f"p=VerificationPolicy((({str(checker)!r},),), '.', 2); "
        f"print(bind_verification_executables(p, Path({str(root)!r})).executables[0].launch_error)"
    )
    completed = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, text=True, timeout=3
    )
    if kind in ("cycle", "depth", "header"):
        assert completed.returncode != 0
        assert "ValueError" in completed.stderr
    else:
        assert completed.returncode == 0
        assert completed.stdout.strip() != "None"


@pytest.mark.parametrize(
    "arguments",
    [
        "-S generic-runtime $ARG",
        "-S generic-runtime \\n",
        "-S generic-runtime #comment",
        "-S -i generic-runtime",
        "-S PATH=/tmp generic-runtime",
        "-S 'unfinished",
    ],
)
def test_ambiguous_env_split_is_refused(tmp_path: Path, arguments: str) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    checker = tmp_path / "checker"
    checker.write_text(f"#!/usr/bin/env {arguments}\n")
    checker.chmod(0o700)
    with pytest.raises(ValueError, match="ambiguous"):
        runner_module.bind_verification_executables(
            VerificationPolicy(((str(checker),),), ".", 2), root
        )


def test_bash_startup_uses_frozen_literal_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    startup = tmp_path / "startup file"
    startup.write_text("export MERCURY_STARTUP=trusted\n")
    planted = root / "startup"
    planted.write_text("export MERCURY_STARTUP=fabricated\n")
    monkeypatch.setenv("BASH_ENV", str(startup))
    policy = VerificationPolicy(
        (("/bin/bash", "-c", 'printf "%s\\n" "$MERCURY_STARTUP"'),), ".", 2
    )
    binding = runner_module.bind_verification_executables(policy, root)
    monkeypatch.setenv("BASH_ENV", str(planted))
    assert (
        ControlledCommandRunner().run(policy, root, selections=binding)[0].stdout
        == "trusted\n"
    )


def test_file_proof_bounds_reads_and_detects_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime"
    path.write_bytes(b"x" * 150000)
    original = os.read
    sizes = []

    def changed(fd: int, count: int) -> bytes:
        sizes.append(count)
        assert 0 < count <= 65536
        result = original(fd, count)
        if len(sizes) == 1:
            with path.open("ab") as output:
                output.write(b"changed")
        return result

    monkeypatch.setattr(os, "read", changed)
    with pytest.raises(OSError, match="changed"):
        runner_module._file_proof(path)
    assert len(sizes) >= 3


@pytest.mark.parametrize(
    "name,value",
    [
        ("PYTHONHOME", "home one:home two"),
        ("LD_PRELOAD", "loader1 loader2"),
        ("DYLD_INSERT_LIBRARIES", "loader one:loader two"),
    ],
)
def test_resolver_value_separators(tmp_path: Path, name: str, value: str) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    separator = " " if name == "LD_PRELOAD" else ":"
    paths = [str(tmp_path / part) for part in value.split(separator)]
    environment = {name: separator.join(paths)}
    frozen = dict(runner_module._resolver_environment(environment, root, [root]))
    assert frozen[name] == ":".join(paths)


@pytest.mark.parametrize(
    "arguments",
    [
        "-S 'generic-runtime' -e",
        '-S "generic-runtime" -e',
        "-S generic-runtime '-e'",
        "'generic-runtime'",
        "-S generic-runtime\u00a0-e",
    ],
)
def test_nonliteral_env_shebang_refuses_batch_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    # Contract: unsupported env syntax refuses the entire batch before launch.
    # No argv rewriting or platform-specific interpretation is guaranteed.
    root = tmp_path / "repository"
    root.mkdir()
    marker = root / "launched"
    (tmp_path / "generic-runtime").symlink_to("/bin/sh")
    checker = tmp_path / "checker"
    checker.write_text(f"#!/usr/bin/env {arguments}\necho unsafe\n")
    checker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    first = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    )
    policy = VerificationPolicy((first, (str(checker),)), ".", 2)
    with pytest.raises(ValueError, match="ambiguous"):
        ControlledCommandRunner().run(policy, root)
    assert not marker.exists()


@pytest.mark.parametrize(
    "declaration,indirect",
    [
        (b"/usr/bin/env \xc2\xa0generic-runtime", False),
        (b"\xc2\xa0/usr/bin/env generic-runtime", False),
        (b"/usr/bin/env\xc2\xa0generic-runtime", False),
        (b"/usr/bin/env generic-runtime\xc2\xa0", False),
        (b"\x0b/usr/bin/env generic-runtime", False),
        (b"/usr/bin/env\x0cgeneric-runtime", False),
        (b"/usr/bin/env generic-runtime\r", False),
        (b"/usr/bin/env \x1cgeneric-runtime", False),
        (b"/usr/bin/env \xc2\xa0generic-runtime", True),
    ],
)
def test_raw_shebang_whitespace_refuses_batch_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declaration: bytes,
    indirect: bool,
) -> None:
    # Contract: raw unsupported whitespace/control syntax refuses the batch
    # before launch, including a declaration in an inspected interpreter chain.
    root = tmp_path / "repository"
    root.mkdir()
    marker = root / "launched"
    (tmp_path / "generic-runtime").symlink_to("/bin/sh")
    checker = tmp_path / "checker"
    malformed = tmp_path / "interpreter" if indirect else checker
    malformed.write_bytes(b"#!" + declaration + b"\necho unsupported\n")
    malformed.chmod(0o700)
    if indirect:
        checker.write_text(f"#!{malformed}\n")
        checker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    first = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    )
    policy = VerificationPolicy((first, (str(checker),)), ".", 2)
    with pytest.raises(ValueError, match="ambiguous"):
        ControlledCommandRunner().run(policy, root)
    assert not marker.exists()


@pytest.mark.parametrize(
    "declaration",
    [
        " /bin/sh\t ",
        "\t/usr/bin/env\tgeneric-runtime",
        " /usr/bin/env\t-S generic-runtime -e\t ",
        "unicode_interpreter",
    ],
)
def test_shebang_preserves_literal_separators_and_unicode_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    runtime = tmp_path / "runtime-λ"
    runtime.symlink_to("/bin/sh")
    (tmp_path / "generic-runtime").symlink_to("/bin/sh")
    checker = tmp_path / "checker"
    if declaration == "unicode_interpreter":
        declaration = str(runtime)
    checker.write_text(f"#!{declaration}\necho supported\n")
    checker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    command = (str(checker),)
    result = ControlledCommandRunner().run(
        VerificationPolicy((command,), ".", 2), root
    )[0]
    assert result.exit_code == 0
    assert result.stdout == "supported\n"
    assert result.command == command

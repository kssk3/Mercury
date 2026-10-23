"""Finite child fixtures exercise profile-local Git resource boundaries."""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_profile import init

from agent_harness import cli
from agent_harness import profile as profile_module


@pytest.fixture
def inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    repository = init(tmp_path / "repository")
    executable = tmp_path / "selected-git"
    monkeypatch.setattr(profile_module, "git_executable", lambda: str(executable))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-git"))
    monkeypatch.setattr(profile_module, "_INVENTORY_STDOUT_LIMIT", 64, raising=False)
    monkeypatch.setattr(profile_module, "_INVENTORY_STDERR_LIMIT", 32, raising=False)
    arguments = [
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository.resolve()),
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "--full-name",
        "-z",
    ]

    def prepare(body: str) -> Path:
        executable.write_text(
            f"#!{sys.executable}\nimport os, signal, sys, time\n"
            "signal.signal(signal.SIGALRM, lambda *_: os._exit(124))\n"
            "signal.setitimer(signal.ITIMER_REAL, 10)\n"
            f"assert sys.argv[1:] == {arguments!r}\n"
            "assert not any(key.startswith('GIT_') for key in os.environ)\n" + body,
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return repository

    return prepare


@pytest.mark.parametrize(
    ("stream", "overflow"),
    [("stdout", False), ("stdout", True), ("stderr", False), ("stderr", True)],
    ids=["stdout-exact", "stdout-overflow", "stderr-exact", "stderr-overflow"],
)
def test_output_boundary(
    inventory: Callable[[str], Path], stream: str, overflow: bool
) -> None:
    payload = b"a.py\0" + b"\0" * 59 if stream == "stdout" else b"x" * 32
    payload += b"x" if overflow else b""
    descriptor = 1 if stream == "stdout" else 2
    body = f"os.write({descriptor}, {payload!r})\n"
    if overflow:
        body += (
            "time.sleep(6.0)\n"
            "open(os.path.join(sys.argv[5], 'overflow-finished'), 'w').close()\n"
        )
    root = inventory(body)
    (root / "a.py").touch()

    if overflow:
        with pytest.raises(ValueError, match=f"Git file inventory: {stream} limit"):
            profile_module.build_repository_profile(root)
        assert not (root / "overflow-finished").exists()
    else:
        result = profile_module.build_repository_profile(root)
        assert result.file_count == (1 if stream == "stdout" else 0)
        assert result.languages == (("Python",) if stream == "stdout" else ())


def test_partial_output_timeout(
    inventory: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready = tmp_path / "partial-output-ready"
    root = inventory(
        f"os.write(1, b'a.py\\0')\nopen({str(ready)!r}, 'w').close()\ntime.sleep(6.0)\n"
    )
    monkeypatch.setattr(profile_module, "_INVENTORY_TIMEOUT_SECONDS", 3.0)
    (root / "a.py").touch()
    with pytest.raises(ValueError, match="Git file inventory: timeout"):
        profile_module.build_repository_profile(root)
    assert ready.exists()


def test_leader_exited_descendant_pipe_deadline(
    inventory: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = tmp_path / "descendant-started"
    marker = tmp_path / "descendant-finished"
    child = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGALRM, lambda *_: os._exit(124))\n"
        "signal.setitimer(signal.ITIMER_REAL, 10)\n"
        f"with open({str(started)!r}, 'w') as ready:\n"
        "    ready.write(str(time.monotonic()))\n"
        "time.sleep(6.0)\n"
        f"open({str(marker)!r}, 'w').close()\n"
    )
    root = inventory(
        "import subprocess\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "os.write(1, b'a.py\\0')\n"
    )
    monkeypatch.setattr(profile_module, "_INVENTORY_TIMEOUT_SECONDS", 3.0)
    (root / "a.py").touch()
    with pytest.raises(ValueError, match="Git file inventory: timeout"):
        profile_module.build_repository_profile(root)
    assert started.exists()
    completion_deadline = float(started.read_text()) + 6.1
    time.sleep(max(0.0, completion_deadline - time.monotonic()))
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["launch", "nonzero"])
def test_process_failure(
    inventory: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = inventory("os.write(1, b'a.py\\0'); os.write(2, b'secret'); sys.exit(7)\n")
    (root / "a.py").touch()
    if failure == "launch":
        monkeypatch.setattr(
            profile_module, "git_executable", lambda: str(root / "missing-program")
        )
    with pytest.raises(ValueError, match="Git file inventory") as caught:
        profile_module.build_repository_profile(root)
    assert "secret" not in str(caught.value)
    assert str(root) not in str(caught.value)


def test_root_timeout_conversion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(path: Path | str) -> Path:
        raise subprocess.TimeoutExpired("secret-command", 10)

    monkeypatch.setattr(profile_module, "resolve_repository_root", timeout)
    with pytest.raises(ValueError, match="Git working tree: timeout"):
        profile_module.build_repository_profile(tmp_path)


def test_nul_filename_chunk_boundary(
    inventory: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = os.fsencode("한글.js")
    second = b"raw-line\nbreak.py"
    data = b"missing\0" * 8191 + first + b"\0" + second + b"\0"
    split = 65529
    root = inventory(
        f"os.write(1, {data[:split]!r}); time.sleep(0.03)\n"
        f"os.write(1, {data[split:]!r})\n"
    )
    monkeypatch.setattr(
        profile_module, "_INVENTORY_STDOUT_LIMIT", 100000, raising=False
    )
    (root / os.fsdecode(first)).touch()
    (root / os.fsdecode(second)).touch()
    result = profile_module.build_repository_profile(root)
    assert result.file_count == 2
    assert result.languages == ("JavaScript", "Python")


def test_concurrent_streams(
    inventory: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = inventory(
        "import threading\n"
        "thread = threading.Thread(target=lambda: os.write(2, b'x' * 131072))\n"
        "thread.start()\n"
        "os.write(1, b'main.py\\0' * 16384)\n"
        "thread.join(timeout=0.8)\n"
    )
    monkeypatch.setattr(
        profile_module, "_INVENTORY_STDOUT_LIMIT", 200000, raising=False
    )
    monkeypatch.setattr(
        profile_module, "_INVENTORY_STDERR_LIMIT", 200000, raising=False
    )
    (root / "main.py").touch()
    result = profile_module.build_repository_profile(root)
    assert result.file_count == 1
    assert result.languages == ("Python",)


def test_cleanup_failure(
    inventory: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready = tmp_path / "cleanup-child-started"
    root = inventory(
        "os.write(1, b'a.py\\0')\n"
        f"with open({str(ready)!r}, 'w') as started:\n"
        "    started.write(str(time.monotonic()))\n"
        "time.sleep(6.0)\n"
    )
    monkeypatch.setattr(profile_module, "_INVENTORY_TIMEOUT_SECONDS", 3.0)
    child_pid: int | None = None

    def denied(group: int, sig: int) -> None:
        nonlocal child_pid
        child_pid = group
        raise PermissionError("secret cleanup details")

    monkeypatch.setattr(os, "killpg", denied)
    with pytest.raises(ValueError, match="Git file inventory: timeout.*cleanup failed"):
        profile_module.build_repository_profile(root)
    assert child_pid is not None
    reap_deadline = time.monotonic() + 10.5
    reaped = 0
    status = 0
    while time.monotonic() < reap_deadline:
        reaped, status = os.waitpid(child_pid, os.WNOHANG)
        if reaped == child_pid:
            break
        time.sleep(0.01)
    assert reaped == child_pid
    assert os.waitstatus_to_exitcode(status) == 0
    assert ready.exists()


def test_empty_success(inventory: Callable[[str], Path]) -> None:
    root = inventory("pass\n")
    result = profile_module.build_repository_profile(root)
    assert result.file_count == 0
    assert result.languages == result.manifests == result.documents == ()
    assert result.top_level_directories == ()


@pytest.mark.parametrize("failure", ["timeout", "stdout", "stderr"])
def test_cli_limit_failure(
    inventory: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    ready = tmp_path / "cli-timeout-ready"
    bodies = {
        "timeout": (
            "os.write(1, b'a.py\\0')\n"
            f"open({str(ready)!r}, 'w').close()\n"
            "time.sleep(6.0)\n"
        ),
        "stdout": "os.write(1, b'a.py\\0' + b'x' * 60)\n",
        "stderr": "os.write(1, b'a.py\\0'); os.write(2, b'secret' * 6)\n",
    }
    root = inventory(bodies[failure])
    if failure == "timeout":
        monkeypatch.setattr(profile_module, "_INVENTORY_TIMEOUT_SECONDS", 3.0)
    (root / "a.py").touch()
    assert cli.main(["profile", str(root)]) == 2
    if failure == "timeout":
        assert ready.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    reason = "timeout" if failure == "timeout" else f"{failure} limit exceeded"
    assert captured.err == (
        f"local-harness: could not inspect Git file inventory: {reason}\n"
    )
    assert captured.err.count("\n") == 1
    assert "secret" not in captured.err
    assert "Traceback" not in captured.err


@pytest.fixture
def owned_inventory(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Intercept stale signals, retaining real finite processes and bounded cleanup."""
    state: dict[str, Any] = {"process": None, "events": [], "wait_error": None}
    original_popen = subprocess.Popen
    original_killpg = os.killpg
    original_wait: Any = None

    def start(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal original_wait
        process = original_popen(*args, **kwargs)
        state["process"] = process
        original_wait = process.wait

        def wait(timeout: float | None = None) -> int:
            state["events"].append("wait")
            error = state["wait_error"]
            if error is not None:
                state["wait_error"] = None
                if state.get("reap_before_error"):
                    original_wait(timeout=timeout)
                raise error
            return int(original_wait(timeout=timeout))

        monkeypatch.setattr(process, "wait", wait)
        return process

    def signal_owned(group: int, sig: int) -> None:
        process = state["process"]
        assert group == process.pid
        if process.returncode is not None:
            state["events"].append("signal_reaped")
            return
        state["events"].append("signal_owned")
        if state.get("deny_signal"):
            raise PermissionError("private cleanup error")
        original_killpg(group, sig)

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(os, "killpg", signal_owned)
    try:
        yield state
    finally:
        process = state["process"]
        if process is not None and process.returncode is None:
            try:
                original_killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            original_wait(timeout=2)


@pytest.mark.parametrize(
    ("stage", "kind"),
    [
        (stage, kind)
        for stage in ("select", "read", "wait")
        for kind in ("keyboard", "system_exit")
    ],
    ids=[
        f"{stage}-{kind}"
        for stage in ("select", "read", "wait")
        for kind in ("keyboard", "system_exit")
    ],
)
def test_inventory_preserves_cancellation(
    inventory: Callable[[str], Path],
    owned_inventory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    kind: str,
) -> None:
    root = inventory(
        "os.write(1, b'a.py\\0')\n"
        + ("os.close(1); os.close(2)\n" if stage == "wait" else "")
        + "time.sleep(6.0)\n"
    )
    error = KeyboardInterrupt() if kind == "keyboard" else SystemExit(37)
    original_read = os.read
    original_selector = selectors.DefaultSelector

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise error

    def selector() -> Any:
        result = original_selector()
        monkeypatch.setattr(result, "select", interrupted)
        return result

    def read(fd: int, size: int) -> bytes:
        process = owned_inventory["process"]
        if process is not None and fd in (
            process.stdout.fileno(),
            process.stderr.fileno(),
        ):
            raise error
        return original_read(fd, size)

    if stage == "select":
        monkeypatch.setattr(selectors, "DefaultSelector", selector)
    elif stage == "read":
        monkeypatch.setattr(os, "read", read)
    else:
        owned_inventory["wait_error"] = error
    # Cleanup failure must not replace the original SystemExit value.
    owned_inventory["deny_signal"] = stage == "select" and kind == "system_exit"
    with pytest.raises(type(error)) as caught:
        profile_module._capture_inventory(root)
    assert caught.value is error
    assert "signal_reaped" not in owned_inventory["events"]


def test_inventory_nonzero_exit_does_not_signal_reaped_group(
    inventory: Callable[[str], Path],
    owned_inventory: dict[str, Any],
) -> None:
    root = inventory("sys.exit(7)\n")
    with pytest.raises(ValueError, match="^could not inspect Git file inventory$"):
        profile_module._capture_inventory(root)
    assert owned_inventory["process"].returncode == 7
    assert "signal_reaped" not in owned_inventory["events"]


def test_inventory_wait_cancellation_does_not_signal_reaped_group(
    inventory: Callable[[str], Path],
    owned_inventory: dict[str, Any],
) -> None:
    root = inventory("pass\n")
    error = KeyboardInterrupt()
    owned_inventory.update(wait_error=error, reap_before_error=True)
    try:
        profile_module._capture_inventory(root)
    except BaseException:
        pass
    assert owned_inventory["process"].returncode == 0
    assert "signal_reaped" not in owned_inventory["events"]


def test_inventory_failure_signals_owned_group_before_reap(
    inventory: Callable[[str], Path],
    owned_inventory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = inventory("os.write(1, b'a.py\\0'); time.sleep(6.0)\n")
    original_selector = selectors.DefaultSelector

    def selector() -> Any:
        result = original_selector()

        def failed(timeout: float | None = None) -> Any:
            raise OSError("private select error")

        monkeypatch.setattr(result, "select", failed)
        return result

    monkeypatch.setattr(selectors, "DefaultSelector", selector)
    with pytest.raises(ValueError, match="^could not inspect Git file inventory$"):
        profile_module._capture_inventory(root)
    assert owned_inventory["events"] == ["signal_owned", "wait"]
    assert owned_inventory["process"].returncode is not None


def test_inventory_eof_does_not_terminate_running_success(
    inventory: Callable[[str], Path],
    owned_inventory: dict[str, Any],
    tmp_path: Path,
) -> None:
    marker = tmp_path / "finished-after-eof"
    root = inventory(
        "os.write(1, b'a.py\\0'); os.close(1); os.close(2)\n"
        "time.sleep(0.15)\n"
        f"open({str(marker)!r}, 'w').close()\n"
    )
    assert profile_module._capture_inventory(root) == b"a.py\0"
    assert marker.exists()
    assert owned_inventory["events"] == ["wait"]

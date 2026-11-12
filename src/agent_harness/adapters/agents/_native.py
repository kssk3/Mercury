"""Shared native execution machinery for trusted agent adapters.

Executable proofs, bounded I/O and cleanup are provider-neutral. Each adapter
owns its native configuration and safety policy.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import time
from pathlib import Path

from agent_harness.adapters._executables import (
    _validate_executable_proofs,
    bind_verification_executables,
)
from agent_harness.diagnostics import DiagnosticsObserver
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.agent import CapturedText
from agent_harness.ports.verification import VerificationExecutable


class _Capture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.raw = bytearray()
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.raw)
        self.raw.extend(chunk[:remaining])
        self.truncated |= len(chunk) > remaining

    def result(self) -> CapturedText:
        return CapturedText(
            self.raw.decode("utf-8", errors="surrogateescape"),
            len(self.raw),
            self.truncated,
        )


class _NativeExecutableBinding:
    """Caller selects a trusted executable; no arbitrary flag/config surface.

    Inherited user configuration, rules, authentication and unrelated environment
    remain active. Resolver paths are checked against the admitted repository
    before every launch. `--ephemeral` is not an environment-isolation promise.
    """

    def __init__(self, executable: Path | str) -> None:
        self._requested_executable = os.fspath(executable)
        if not self._requested_executable or "\x00" in self._requested_executable:
            raise ValueError("executable must be nonempty and NUL-free")
        self._executable: str | None = None
        self._selected_path: Path | None = None
        self._selection_error: OSError | None = None
        self._selection_admitted = False
        self._launch_environment: dict[str, str] | None = None
        self._native_selection: VerificationExecutable | None = None
        self._native_interpreter_proofs: dict[str, str] = {}
        requested = Path(self._requested_executable)
        # Relative direct paths retain Popen's repository-cwd meaning. Names and
        # absolute paths can be bound before the repository is admitted.
        if requested.is_absolute() or os.sep not in self._requested_executable:
            self._bind_executable(None)

    def prepare(self, repository_root: Path) -> None:
        """Bind and validate after the harness saves its snapshot and intent."""
        self._bind_executable(repository_root)

    def _bind_executable(self, root: Path | None) -> None:
        if self._executable is None and self._selection_error is None:
            requested = self._requested_executable
            if os.sep not in requested:
                selected = shutil.which(requested)
                if selected is None:
                    self._selection_error = FileNotFoundError(
                        f"native executable unavailable: {requested}"
                    )
                    return
                path = Path(selected).absolute()
            else:
                path = Path(requested)
                if not path.is_absolute():
                    if root is None:
                        return
                    path = root / path
                path = Path(os.path.abspath(path))
            self._selected_path = path
            self._executable = str(path.resolve())
        if root is not None and self._selected_path is not None:
            if self._selected_path.is_relative_to(root) or Path(
                self._executable or self._selected_path
            ).is_relative_to(root):
                raise ValueError("native executable must be outside the repository")
            if self._executable is not None and self._selection_error is None:
                # Inspect the initial selected traversal as well as the fixed
                # entry. Later turns retain alias-selection continuity while
                # rechecking the actual native interpreter chain and environment.
                commands: tuple[tuple[str, ...], ...] = ((self._executable,),)
                if not self._selection_admitted:
                    commands = ((str(self._selected_path),), *commands)
                bound = bind_verification_executables(
                    VerificationPolicy(commands, ".", 1), root
                )
                for entry in bound.executables:
                    if entry.launch_error is not None:
                        self._selection_error = OSError(entry.launch_error)
                        break
                if self._selection_error is None:
                    if self._native_selection is None:
                        self._native_selection = bound.executables[-1]
                        self._native_interpreter_proofs = dict(bound.interpreter_proofs)
                    else:
                        try:
                            if any(
                                self._native_interpreter_proofs.get(path) != digest
                                for path, digest in bound.interpreter_proofs
                            ):
                                raise OSError(
                                    "native interpreter changed after admission"
                                )
                            _validate_executable_proofs(
                                self._native_selection, bound.interpreter_proofs
                            )
                        except OSError as failure:
                            self._selection_error = failure
                self._selection_admitted = True
                environment = os.environ.copy()
                for name, value in bound.resolver_environment:
                    if value is None:
                        environment.pop(name, None)
                    else:
                        environment[name] = value
                self._launch_environment = environment


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    # Leader exit does not mean descendants have released inherited pipes.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _communicate_bounded(
    process: subprocess.Popen[bytes],
    prompt: bytes,
    stdout: _Capture,
    stderr: _Capture,
    started: float,
    timeout: float,
    observer: DiagnosticsObserver | None = None,
) -> bool:
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    streams = (process.stdin, process.stdout, process.stderr)
    sent = 0
    stdin_complete = False
    timed_out = False
    cleanup_started: float | None = None
    with selectors.DefaultSelector() as selector:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(
                stream,
                selectors.EVENT_WRITE
                if stream is process.stdin
                else selectors.EVENT_READ,
            )
        try:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if observer is not None and process.poll() is not None:
                    observer.mark("process_exit", now - started)
                remaining = (
                    timeout - (now - started)
                    if cleanup_started is None
                    else 1.0 - (now - cleanup_started)
                )
                if remaining <= 0:
                    if cleanup_started is not None:
                        break
                    timed_out = True
                    if observer is not None:
                        observer.mark("timeout", now - started)
                        observer.mark("cleanup_started", now - started)
                    _kill_group(process)
                    cleanup_started = now
                    if not process.stdin.closed:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                for key, _ in selector.select(min(remaining, 0.05)):
                    ready_stream = key.fileobj
                    if ready_stream is process.stdin:
                        try:
                            sent += os.write(key.fd, prompt[sent : sent + 65536])
                            stdin_complete = sent == len(prompt)
                        except BrokenPipeError:
                            sent = len(prompt)
                        except BlockingIOError:
                            continue
                        if sent == len(prompt):
                            if observer is not None:
                                observer.mark(
                                    "stdin_complete"
                                    if stdin_complete
                                    else "stdin_incomplete",
                                    time.monotonic() - started,
                                )
                            selector.unregister(key.fd)
                            process.stdin.close()
                    else:
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        channel = (
                            "stdout" if ready_stream is process.stdout else "stderr"
                        )
                        if observer is not None:
                            if chunk:
                                observer.output(
                                    channel, chunk, time.monotonic() - started
                                )
                            else:
                                observer.eof(channel, time.monotonic() - started)
                        if not chunk:
                            selector.unregister(key.fd)
                            if ready_stream is process.stdout:
                                process.stdout.close()
                            else:
                                process.stderr.close()
                        else:
                            (stdout if ready_stream is process.stdout else stderr).add(
                                chunk
                            )
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                timed_out = True
                if observer is not None:
                    observer.mark("timeout", time.monotonic() - started)
                    observer.mark("cleanup_started", time.monotonic() - started)
                _kill_group(process)
        except BaseException:
            if observer is not None:
                observer.mark("cleanup_started", time.monotonic() - started)
            _kill_group(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            if observer is not None:
                elapsed = time.monotonic() - started
                if not stdin_complete:
                    observer.mark("stdin_incomplete", elapsed)
                if process.poll() is not None:
                    observer.mark("process_exit", elapsed)
                if timed_out:
                    observer.mark("cleanup_finished", elapsed)
            for stream in streams:
                stream.close()
    return timed_out

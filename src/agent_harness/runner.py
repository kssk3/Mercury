from __future__ import annotations

import hashlib
import os
import selectors
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_harness.policy import VerificationPolicy
from agent_harness.repository import _git_query, git_read_environment


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class VerificationExecutable:
    command: tuple[str, ...]
    selected: str | None
    resolved: str | None
    sha256: str | None
    launch_error: str | None
    identity: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class VerificationExecutables:
    repository: str
    working_directory: str
    executables: tuple[VerificationExecutable, ...]
    resolver_environment: tuple[tuple[str, str | None], ...] = ()
    interpreter_proofs: tuple[tuple[str, str], ...] = ()


def _within_verification_boundary(path: Path, boundaries: list[Path]) -> bool:
    """Compare ancestry by filesystem identity as well as lexical spelling.

    Case aliases retain their spelling after resolve() on some filesystems.
    Missing leaves still have observable ancestors and stay typed launch errors;
    other inspection failures cannot establish an external selection.
    """
    if any(path.is_relative_to(boundary) for boundary in boundaries):
        return True
    try:
        identities = {
            (info.st_dev, info.st_ino)
            for boundary in boundaries
            for info in (boundary.stat(),)
        }
        for ancestor in (path, *path.parents):
            try:
                info = ancestor.stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            if (info.st_dev, info.st_ino) in identities:
                return True
    except OSError as failure:
        raise ValueError(
            "could not inspect verification executable boundary"
        ) from failure
    return False


def _check_external_traversal(path: Path, boundaries: list[Path]) -> None:
    """Reject mutable target boundaries anywhere in a selected symlink chain."""
    current = Path(path.anchor)
    pending = list(path.parts[1:])
    links = 0
    through_link = False
    while pending:
        part = pending.pop(0)
        if part == ".":
            continue
        if part == "..":
            current = current.parent
            continue
        current /= part
        if (
            through_link or not pending or current.is_symlink()
        ) and _within_verification_boundary(current, boundaries):
            raise ValueError(
                "verification executable must be outside repository and Git metadata"
            )
        if current.is_symlink():
            through_link = True
            links += 1
            if links > 40:
                raise ValueError("verification executable has too many symbolic links")
            target = Path(os.readlink(current))
            if target.is_absolute():
                current = Path(target.anchor)
                pending = list(target.parts[1:]) + pending
            else:
                current = current.parent
                pending = list(target.parts) + pending


# Only resolver configuration enters the serialized bundle, never credentials.
_RESOLVER_PATHS = (
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "DYLD_FALLBACK_FRAMEWORK_PATH",
    "PYTHONPATH",
    "RUBYLIB",
    "PERL5LIB",
    "PERLLIB",
    "NODE_PATH",
)
_RESOLVER_FILES = (
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "ENV",
    "BASH_ENV",
)


def _external_path(path: Path, boundaries: list[Path]) -> Path:
    _check_external_traversal(path, boundaries)
    selected = path.parent.resolve() / path.name
    if _within_verification_boundary(
        selected, boundaries
    ) or _within_verification_boundary(selected.resolve(), boundaries):
        raise ValueError(
            "verification executable must be outside repository and Git metadata"
        )
    return selected


def _resolver_environment(
    environment: dict[str, str], directory: Path, boundaries: list[Path]
) -> tuple[tuple[str, str | None], ...]:
    configured: list[tuple[str, str | None]] = []
    for name in _RESOLVER_PATHS:
        value = environment.get(name, os.defpath if name == "PATH" else None)
        if value is None:
            configured.append((name, None))
            continue
        entries = []
        for entry in value.split(os.pathsep):
            try:
                entries.append(
                    str(_external_path(directory / entry, boundaries).resolve())
                )
            except ValueError:
                continue
        # Empty PATH searches cwd; use a guaranteed nonexistent external leaf.
        configured.append(
            (
                name,
                os.pathsep.join(entries)
                if entries
                else ("/dev/null/mercury-no-search" if name == "PATH" else None),
            )
        )
    for name in _RESOLVER_FILES:
        value = environment.get(name)
        if value:
            if any(character in value for character in "$`\\~\n\r"):
                raise ValueError("ambiguous verification loader configuration")
            if name == "LD_PRELOAD":
                pieces = value.replace(" ", os.pathsep).split(os.pathsep)
            elif name in ("DYLD_INSERT_LIBRARIES", "PYTHONHOME"):
                pieces = value.split(os.pathsep)
            else:
                pieces = [value]
            if name == "PYTHONHOME" and len(pieces) > 2:
                raise ValueError("ambiguous verification loader configuration")
            for entry in pieces:
                if not entry:
                    raise ValueError("ambiguous verification loader configuration")
                _external_path(directory / entry, boundaries)
            value = os.pathsep.join(str(directory / entry) for entry in pieces)
        configured.append((name, value))
    return tuple(configured)


def _file_proof(path: Path) -> tuple[str, tuple[int, int, int], bytes]:
    """Read one opened regular file, with finite size and retained-header bounds."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("verification executable must be a regular file")
        digest = hashlib.sha256()
        remaining = info.st_size
        header = b""
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise OSError("verification executable changed during selection")
            digest.update(chunk)
            header += chunk[: max(0, 4096 - len(header))]
            remaining -= len(chunk)
        after = os.fstat(fd)
        if os.read(fd, 1) or (
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_mode,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode):
            raise OSError("verification executable changed during selection")
        return digest.hexdigest(), (info.st_dev, info.st_ino, info.st_mode), header
    finally:
        os.close(fd)


def _env_program(arguments: str) -> str:
    # Native kernels/env variants disagree on quoting. Admit only the shared
    # unquoted literal profile, without rewriting the original invocation.
    if any(
        character in "\"'$`\\#" or not (" " <= character <= "~" or character == "\t")
        for character in arguments
    ):
        raise ValueError("ambiguous verification env shebang")
    if arguments.startswith("-S ") or arguments.startswith("-S\t"):
        words = arguments[3:].split()
    else:
        words = arguments.split()
        if len(words) != 1:
            raise ValueError("ambiguous verification env shebang")
    if not words or words[0].startswith("-") or "=" in words[0]:
        raise ValueError("ambiguous verification env shebang")
    return words[0]


def _bind_interpreters(
    header: bytes,
    boundaries: list[Path],
    search_path: str,
    proofs: dict[str, str],
    seen: set[tuple[int, int]],
) -> None:
    if not header.startswith(b"#!"):
        return
    if len(header) == 4096 and b"\n" not in header:
        raise ValueError("verification shebang exceeds header limit")
    try:
        raw_declaration = header.split(b"\n", 1)[0][2:].decode("utf-8")
    except UnicodeError as failure:
        raise ValueError("invalid verification shebang") from failure
    # Validate before normalization: Unicode split/byte strip would otherwise
    # consume unsupported delimiters before the env argument validator sees them.
    if any(
        (character.isspace() and character not in " \t")
        or (ord(character) < 32 and character != "\t")
        or ord(character) == 127
        for character in raw_declaration
    ):
        raise ValueError("ambiguous verification shebang")
    declaration = raw_declaration.strip(" \t").split(maxsplit=1)
    if not declaration or not Path(declaration[0]).is_absolute():
        raise ValueError("verification shebang requires an absolute interpreter")
    interpreter = _external_path(Path(declaration[0]), boundaries)

    def inspect(target: Path) -> None:
        if len(seen) >= 8:
            raise ValueError("recursive verification shebang depth limit")
        digest, identity, child_header = _file_proof(target)
        physical = identity[:2]
        if physical in seen:
            raise ValueError("recursive verification shebang")
        proofs[str(target)] = digest
        _bind_interpreters(
            child_header, boundaries, search_path, proofs, seen | {physical}
        )

    inspect(interpreter)
    if interpreter.resolve() == Path("/usr/bin/env").resolve():
        if len(declaration) != 2:
            raise ValueError("ambiguous verification env shebang")
        program = shutil.which(_env_program(declaration[1]), path=search_path)
        if program is None:
            raise OSError("verification shebang interpreter unavailable")
        inspect(_external_path(Path(program), boundaries))


def bind_verification_executables(
    policy: VerificationPolicy, repository: Path | str
) -> VerificationExecutables:
    """Select caller-managed external programs before writable work.

    Paths preserve external virtualenv invocation semantics. This point-in-time
    selection and content proof does not confine processes or freeze host files.
    """
    root = Path(repository).resolve()
    directory = (root / policy.working_directory).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("policy working directory must stay inside the repository")
    boundaries = [root]
    for selector in ("--absolute-git-dir", "--git-common-dir"):
        query = _git_query(root, selector)
        if query.returncode:
            if query.returncode == 128 and query.stderr.startswith(
                "fatal: not a git repository"
            ):
                break
            raise ValueError("could not inspect verification Git metadata")
        metadata = Path(query.stdout.removesuffix("\n"))
        if not metadata.is_absolute():
            metadata = root / metadata
        boundaries.extend((Path(os.path.abspath(metadata)), metadata.resolve()))
    environment = git_read_environment()
    search_path = os.pathsep.join(
        str(directory / entry)
        for entry in environment.get("PATH", os.defpath).split(os.pathsep)
    )
    resolver_environment = _resolver_environment(environment, directory, boundaries)
    safe_path = dict(resolver_environment)["PATH"]
    assert safe_path is not None
    interpreter_proofs: dict[str, str] = {}
    selections = []
    for command in policy.commands:
        requested = command[0]
        selected: str | None
        if os.sep in requested:
            selected = str(directory / requested)
        else:
            selected = shutil.which(requested, path=search_path)
        if selected is None:
            selections.append(
                VerificationExecutable(
                    command,
                    None,
                    None,
                    None,
                    f"verification executable unavailable: {requested}",
                )
            )
            continue
        path = Path(selected)
        _check_external_traversal(path, boundaries)
        # Resolve directory traversal before native work, while retaining the
        # final executable symlink that identifies an external virtualenv.
        path = path.parent.resolve() / path.name
        resolved = path.resolve()
        if _within_verification_boundary(
            Path(os.path.normpath(path)), boundaries
        ) or _within_verification_boundary(resolved, boundaries):
            raise ValueError(
                "verification executable must be outside repository and Git metadata"
            )
        digest = None
        error = None
        identity = None
        try:
            digest, identity, header = _file_proof(path)
            _bind_interpreters(
                header, boundaries, safe_path, interpreter_proofs, {identity[:2]}
            )
        except OSError as failure:
            error = str(failure)
        selections.append(
            VerificationExecutable(
                command,
                str(path),
                str(resolved),
                digest if error is None else None,
                error,
                identity,
            )
        )
    return VerificationExecutables(
        str(root),
        str(directory),
        tuple(selections),
        resolver_environment,
        tuple(sorted(interpreter_proofs.items())),
    )


_TRUNCATION_MARKER = b"\n[output truncated]\n"


def _validate_executable_proofs(
    selection: VerificationExecutable,
    interpreter_proofs: tuple[tuple[str, str], ...],
) -> None:
    """Compare finite fresh reads with the admitted external program proofs."""
    if selection.launch_error is not None:
        raise FileNotFoundError(selection.launch_error)
    if selection.selected is None or selection.sha256 is None:
        raise OSError("verification executable has no admitted proof")
    digest, identity, _ = _file_proof(Path(selection.selected))
    if digest != selection.sha256 or identity != selection.identity:
        raise OSError("verification executable changed after selection")
    for path, expected in interpreter_proofs:
        digest, _, _ = _file_proof(Path(path))
        if digest != expected:
            raise OSError("verification interpreter changed after selection")


class ControlledCommandRunner:
    """Capture at most max_output_bytes per stream, including truncation notice."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < len(_TRUNCATION_MARKER)
        ):
            raise ValueError(
                f"max_output_bytes must be an integer >= {len(_TRUNCATION_MARKER)}"
            )
        self._monotonic = monotonic
        self._max_output_bytes = max_output_bytes

    def run(
        self,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        repository_root = Path(repository).resolve()
        working_directory = (repository_root / policy.working_directory).resolve()
        if not working_directory.is_relative_to(repository_root):
            raise ValueError("policy working directory must stay inside the repository")

        selected = selections or bind_verification_executables(policy, repository_root)
        if selected.repository != str(
            repository_root
        ) or selected.working_directory != str(working_directory):
            raise ValueError(
                "verification selection does not match repository or directory"
            )
        by_command = {item.command: item for item in selected.executables}
        if any(command not in by_command for command in policy.commands):
            raise ValueError("verification selection does not match commands")
        environment = git_read_environment()
        for name, value in selected.resolver_environment:
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return tuple(
            self._run_command(
                command,
                working_directory,
                policy.timeout_seconds,
                by_command[command],
                environment,
                selected.interpreter_proofs,
            )
            for command in policy.commands
        )

    def _run_command(
        self,
        command: tuple[str, ...],
        working_directory: Path,
        timeout_seconds: int,
        selection: VerificationExecutable,
        environment: dict[str, str],
        interpreter_proofs: tuple[tuple[str, str], ...],
    ) -> CommandResult:
        started_at = self._monotonic()
        try:
            _validate_executable_proofs(selection, interpreter_proofs)
            process = subprocess.Popen(
                (selection.selected or command[0], *command[1:]),
                cwd=working_directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=environment,
            )
        except OSError as error:
            return CommandResult(
                command=command,
                exit_code=None,
                stdout="",
                stderr=str(error),
                duration_seconds=self._monotonic() - started_at,
                timed_out=False,
            )

        timed_out = False
        stdout = _OutputBuffer(self._max_output_bytes)
        stderr = _OutputBuffer(self._max_output_bytes)
        try:
            _collect_output(process, stdout, stderr, timeout_seconds)
        except BaseException as failure:
            timed_out = isinstance(failure, subprocess.TimeoutExpired)
            # The leader may have exited while descendants still hold the pipes.
            # Its isolated group must be killed even in that case.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                _collect_output(process, stdout, stderr, 1)
            except BaseException:
                # Cleanup failures must not replace the original collection error.
                # Escaped descendants can retain pipes; reaping stays bounded.
                try:
                    process.wait(timeout=1)
                except BaseException:
                    pass
            if not timed_out:
                # Every non-timeout failure propagates after bounded cleanup.
                raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

        return CommandResult(
            command=command,
            exit_code=None if timed_out else process.returncode,
            stdout=stdout.text(),
            stderr=stderr.text(),
            duration_seconds=self._monotonic() - started_at,
            timed_out=timed_out,
        )


def _as_text(output: str | bytes | None) -> str:
    """Preserve bytes via UTF-8; encode with surrogateescape to recover them."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="surrogateescape")
    return output


class _OutputBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.data)
        self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True

    def text(self) -> str:
        if self.truncated:
            prefix = self.data[: self.limit - len(_TRUNCATION_MARKER)]
            return _as_text(bytes(prefix) + _TRUNCATION_MARKER)
        return _as_text(bytes(self.data))


def _collect_output(
    process: subprocess.Popen[bytes],
    stdout: _OutputBuffer,
    stderr: _OutputBuffer,
    timeout: int,
) -> None:
    # Calculating this deadline deliberately preserves huge-timeout OverflowError.
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for stream, output in ((process.stdout, stdout), (process.stderr, stderr)):
            if stream is not None and not stream.closed:
                selector.register(stream, selectors.EVENT_READ, output)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    key.data.append(chunk)
                else:
                    selector.unregister(key.fileobj)
        process.wait(timeout=max(0, deadline - time.monotonic()))

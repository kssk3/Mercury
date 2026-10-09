from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
from agent_harness.admission import record_admission
from agent_harness.context import build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal

if TYPE_CHECKING:
    from agent_harness.native_output import StoredScopedTurnResult


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = (tmp_path / "repository").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "base.txt").write_text("baseline")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
    )
    return root


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\nimport sys, pathlib\nsys.stdin.buffer.read()\n" + body
    )
    executable.chmod(0o700)
    return executable


def redact(text: str) -> str:
    return text.replace("SENTINEL-SECRET", "[removed]")


def invoke(
    repository: Path,
    executable: Path,
    journal: EventJournal,
    *,
    redactor: Callable[[str], str] = redact,
    max_output_bytes: int = 128,
    budgets: TurnBudgets | None = None,
) -> StoredScopedTurnResult:
    from agent_harness.native_output import run_stored_scoped_turn

    task = TaskContract("Fixture prompt", ("base.txt",), (), ("Separate verification",))
    return run_stored_scoped_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-02T00:00:00Z"),
        build_context_packet(repository, source_paths=["base.txt"], budget_bytes=4096),
        journal=journal,
        budgets=budgets or TurnBudgets(2, 16000, 128, 128, 128),
        redactor=redactor,
        max_output_bytes=max_output_bytes,
    )


def payload(final: str = "SENTINEL-SECRET final") -> str:
    return (
        "print('SENTINEL-SECRET out', flush=True)\n"
        "print('SENTINEL-SECRET err', file=sys.stderr, flush=True)\n"
        f"pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text({final!r})\n"
    )


def test_one_scoped_turn_persists_redacted_channels_and_correlated_metadata(
    repository: Path,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "launches"
    body = (
        f"with pathlib.Path({str(marker)!r}).open('a') as stream: stream.write('x')\n"
    )
    journal = EventJournal(tmp_path / "state")
    result = invoke(repository, fake(tmp_path, body + payload()), journal)
    assert marker.read_text() == "x"
    assert result.stdout.path.read_bytes() == b"[removed] out\n"
    assert result.stderr.path.read_bytes() == b"[removed] err\n"
    assert result.final_message is not None
    assert result.final_message.path.read_bytes() == b"[removed] final"
    assert result.scoped_turn.decision.proceed
    assert (
        "SENTINEL-SECRET" in result.scoped_turn.observed_turn.adapter_result.stdout.text
    )
    events = journal.read()
    assert [event["event"] for event in events] == [
        "turn_intent",
        "turn_observation",
        "scope_decision",
        "native_output_stored",
    ]
    terminal = events[-1]
    assert terminal["attempt_id"] == result.scoped_turn.observed_turn.attempt_id
    assert (
        terminal["repository_id"]
        == result.scoped_turn.observed_turn.after.repository_id
    )
    assert terminal["stdout"] == {
        "identifier": result.stdout.identifier,
        "byte_count": 14,
        "truncated": False,
        "captured_bytes": 20,
        "capture_truncated": False,
    }
    persisted = journal.path.read_text() + "".join(
        path.read_text() for path in (tmp_path / "state" / "outputs").iterdir()
    )
    for secret in ("SENTINEL-SECRET", "Fixture prompt", "Separate verification"):
        assert secret not in persisted
    assert "[removed]" not in journal.path.read_text()
    import hashlib

    for raw in (
        b"SENTINEL-SECRET out\n",
        b"SENTINEL-SECRET err\n",
        b"SENTINEL-SECRET final",
    ):
        assert hashlib.sha256(raw).hexdigest() not in journal.path.read_text()
    with pytest.raises(FrozenInstanceError):
        result.stdout = result.stderr  # type: ignore[misc]


@pytest.mark.parametrize("failure", ["raised", "nonstring", "encoding"])
def test_redaction_or_encoding_failure_is_fixed_safe_and_retains_scoped_turn(
    repository: Path,
    tmp_path: Path,
    failure: str,
) -> None:
    from agent_harness.native_output import NativeOutputError

    def reject(text: str) -> str:
        if failure == "raised":
            raise RuntimeError("SENTINEL-SECRET exception")
        if failure == "nonstring":
            return None  # type: ignore[return-value]
        return "\udcff"

    journal = EventJournal(tmp_path / "state")
    with pytest.raises(NativeOutputError) as error:
        invoke(repository, fake(tmp_path, payload()), journal, redactor=reject)
    assert error.value.code == (
        "encoding_failed" if failure == "encoding" else "redaction_failed"
    )
    assert not error.value.proceed
    assert (
        error.value.scoped_turn.observed_turn.adapter_result.process_status
        == "successful_exit"
    )
    assert str(error.value) == error.value.code
    import traceback

    assert "SENTINEL-SECRET exception" not in "".join(
        traceback.format_exception(error.value)
    )
    assert error.value.__suppress_context__
    assert not (tmp_path / "state" / "outputs").exists()
    assert [event["event"] for event in journal.read()] == [
        "turn_intent",
        "turn_observation",
        "scope_decision",
    ]


@pytest.mark.parametrize("cap", [0, -1, True, 2.5, "2", None])
def test_invalid_output_cap_rejects_before_native_or_journal_side_effect(
    repository: Path,
    tmp_path: Path,
    cap: object,
) -> None:
    marker = tmp_path / "ran"
    journal = EventJournal(tmp_path / "state")
    with pytest.raises((ValueError, TypeError)):
        invoke(
            repository,
            fake(tmp_path, f"pathlib.Path({str(marker)!r}).touch()\n"),
            journal,
            max_output_bytes=cap,  # type: ignore[arg-type]
        )
    assert not marker.exists() and not journal.path.exists()


def test_noncallable_redactor_rejects_before_native_or_journal_side_effect(
    repository: Path,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(TypeError):
        invoke(
            repository,
            fake(tmp_path, f"pathlib.Path({str(marker)!r}).touch()\n"),
            journal,
            redactor=None,  # type: ignore[arg-type]
        )
    assert not marker.exists() and not journal.path.exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        "outputs_link",
        "dangling_outputs",
        "outputs_file",
        "repository",
        "alias",
        "parent_link",
    ],
)
def test_unsafe_storage_location_rejects_before_native_side_effect(
    repository: Path,
    tmp_path: Path,
    unsafe: str,
) -> None:
    marker = tmp_path / "ran"
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    if unsafe in {"outputs_link", "dangling_outputs", "outputs_file"}:
        state.mkdir()
        if unsafe == "outputs_file":
            (state / "outputs").write_text("untouched")
        else:
            (state / "outputs").symlink_to(
                outside if unsafe == "outputs_link" else tmp_path / "absent",
                target_is_directory=True,
            )
    elif unsafe == "repository":
        state = repository / "state"
    elif unsafe == "alias":
        state = state / ".." / "state"
    else:
        state.symlink_to(outside, target_is_directory=True)
    journal = EventJournal(state)
    with pytest.raises((ValueError, TypeError)):
        invoke(
            repository,
            fake(tmp_path, f"pathlib.Path({str(marker)!r}).touch()\n"),
            journal,
        )
    assert not marker.exists()
    assert not journal.path.exists()
    assert not list(outside.iterdir())


def test_external_journal_outputs_equal_repository_rejects_before_side_effects(
    repository: Path,
    tmp_path: Path,
) -> None:
    state = (tmp_path / "state").resolve()
    state.mkdir()
    root = repository.rename(state / "outputs")
    before = {path.name for path in root.iterdir()}
    marker = tmp_path / "ran"
    journal = EventJournal(state)

    with pytest.raises(ValueError):
        invoke(
            root,
            fake(tmp_path, f"pathlib.Path({str(marker)!r}).touch()\n"),
            journal,
        )

    assert not marker.exists()
    assert not journal.path.exists()
    assert {path.name for path in root.iterdir()} == before
    assert (root / "base.txt").read_text() == "baseline"


@pytest.mark.parametrize(
    "boundary",
    [
        "redactor_second",
        "storage_second",
        "late_state",
        "late_outputs",
        "append",
        "terminal_path",
    ],
)
def test_late_failure_preserves_safe_partial_outputs_without_completion(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from collections.abc import Mapping

    from agent_harness.native_output import NativeOutputError
    from agent_harness.output import OutputStore, StoredOutput

    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()

    class FailingJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if event["event"] == "native_output_stored" and boundary == "append":
                raise OSError("SENTINEL-SECRET exception")
            super().append(event)

    journal = FailingJournal(state)
    calls = 0

    def callback(text: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            if boundary == "redactor_second":
                raise RuntimeError("SENTINEL-SECRET exception")
            if boundary == "late_state":
                state.rename(tmp_path / "retained-state")
                state.symlink_to(outside, target_is_directory=True)
            if boundary == "late_outputs":
                (state / "outputs").rename(state / "retained-outputs")
                (state / "outputs").symlink_to(outside, target_is_directory=True)
        return redact(text)

    real_write = OutputStore.write
    writes = 0

    def write(store: OutputStore, text: str) -> StoredOutput:
        nonlocal writes
        writes += 1
        if writes == 2 and boundary == "storage_second":
            raise OSError("SENTINEL-SECRET exception")
        output = real_write(store, text)
        if writes == 3 and boundary == "terminal_path":
            journal.path.write_text(journal.path.read_text() + "incomplete")
        return output

    monkeypatch.setattr(OutputStore, "write", write)
    with pytest.raises(NativeOutputError) as error:
        invoke(repository, fake(tmp_path, payload()), journal, redactor=callback)
    expected = (
        "redaction_failed"
        if boundary == "redactor_second"
        else (
            "output_record_failed"
            if boundary in {"append", "terminal_path"}
            else "storage_failed"
        )
    )
    assert error.value.code == expected and not error.value.proceed
    assert (
        error.value.scoped_turn.observed_turn.adapter_result.process_status
        == "successful_exit"
    )
    import traceback

    assert "SENTINEL-SECRET exception" not in "".join(
        traceback.format_exception(error.value)
    )
    assert not list(outside.iterdir())
    retained = tmp_path / "retained-state" if boundary == "late_state" else state
    output_dir = retained / (
        "retained-outputs" if boundary == "late_outputs" else "outputs"
    )
    artifacts = list(output_dir.glob("*.txt"))
    assert len(artifacts) == (3 if boundary in {"append", "terminal_path"} else 1)
    assert all("SENTINEL-SECRET" not in artifact.read_text() for artifact in artifacts)
    assert "native_output_stored" not in (retained / "events.jsonl").read_text()


@pytest.mark.parametrize("final", [None, ""])
def test_missing_final_has_no_artifact_but_empty_available_final_is_stored(
    repository: Path,
    tmp_path: Path,
    final: str | None,
) -> None:
    body = "" if final is None else payload(final)
    journal = EventJournal(tmp_path / "state")
    result = invoke(repository, fake(tmp_path, body), journal)
    if final is None:
        assert result.final_message is None
        assert journal.read()[-1]["final_message"] is None
    else:
        assert result.final_message is not None
        assert result.final_message.path.read_bytes() == b""
        assert (
            result.final_message.byte_count == 0 and not result.final_message.truncated
        )


def test_per_channel_utf8_storage_cap_and_content_deduplication(
    repository: Path,
    tmp_path: Path,
) -> None:
    body = "sys.stdout.write('가나다')\nsys.stderr.write('가나다')\n"
    journal = EventJournal(tmp_path / "state")
    result = invoke(repository, fake(tmp_path, body), journal, max_output_bytes=5)
    assert (
        result.stdout.path.read_bytes()
        == result.stderr.path.read_bytes()
        == "가".encode()
    )
    assert result.stdout.identifier == result.stderr.identifier
    assert result.stdout.byte_count == result.stderr.byte_count == 3
    assert result.stdout.truncated and result.stderr.truncated
    assert len(list((tmp_path / "state" / "outputs").iterdir())) == 1
    terminal = journal.read()[-1]
    assert (
        terminal["stdout"]
        == terminal["stderr"]
        == {
            "identifier": result.stdout.identifier,
            "byte_count": 3,
            "truncated": True,
            "captured_bytes": 9,
            "capture_truncated": False,
        }
    )


@pytest.mark.parametrize("channel", ["stdout", "stderr", "final_message"])
@pytest.mark.parametrize("normalize", [False, True])
def test_opaque_invalid_native_bytes_require_explicit_callback_normalization(
    repository: Path,
    tmp_path: Path,
    channel: str,
    normalize: bool,
) -> None:
    from agent_harness.native_output import NativeOutputError

    raw = b"\xff\xe2\x82\xac!"
    if channel == "final_message":
        body = f"pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_bytes({raw!r})\n"
    else:
        body = f"sys.{channel}.buffer.write({raw!r})\n"
    seen: list[str] = []

    def callback(text: str) -> str:
        seen.append(text)
        return (
            text.encode("utf-8", errors="surrogateescape").decode(
                "utf-8", errors="replace"
            )
            if normalize
            else text
        )

    journal = EventJournal(tmp_path / "state")
    executable = fake(tmp_path, body)
    if not normalize:
        with pytest.raises(NativeOutputError) as error:
            invoke(repository, executable, journal, redactor=callback)
        assert error.value.code == "encoding_failed"
        assert "native_output_stored" not in journal.path.read_text()
    else:
        result = invoke(repository, executable, journal, redactor=callback)
        output = getattr(result, channel)
        assert output is not None and output.path.read_bytes() == "�€!".encode()
    assert raw.decode("utf-8", errors="surrogateescape") in seen


def test_capture_partial_utf8_and_storage_truncation_remain_separate(
    repository: Path,
    tmp_path: Path,
) -> None:
    journal = EventJournal(tmp_path / "state")
    result = invoke(
        repository,
        fake(tmp_path, "sys.stdout.buffer.write('가나다'.encode())\n"),
        journal,
        budgets=TurnBudgets(2, 16000, 7, 128, 128),
        max_output_bytes=5,
        redactor=lambda text: text.encode("utf-8", errors="surrogateescape").decode(
            "utf-8", errors="replace"
        ),
    )
    capture = result.scoped_turn.observed_turn.adapter_result.stdout
    assert (
        capture.text.encode("utf-8", errors="surrogateescape")
        == b"\xea\xb0\x80\xeb\x82\x98\xeb"
    )
    assert result.stdout.path.read_bytes() == "가".encode()
    assert journal.read()[-1]["stdout"] == {
        "identifier": result.stdout.identifier,
        "byte_count": 3,
        "truncated": True,
        "captured_bytes": 7,
        "capture_truncated": True,
    }


@pytest.mark.parametrize(
    "status", ["successful_exit", "nonzero_exit", "timeout", "launch_error"]
)
@pytest.mark.parametrize("violate", [False, True])
def test_every_complete_process_status_is_stored_without_upgrading_scope_decision(
    repository: Path,
    tmp_path: Path,
    status: str,
    violate: bool,
) -> None:
    body = payload()
    if violate:
        body += "pathlib.Path('outside').write_text('changed')\n"
    if status == "nonzero_exit":
        body += "sys.exit(7)\n"
    elif status == "timeout":
        body += "import time\ntime.sleep(30)\n"
    executable = (
        tmp_path / "missing" if status == "launch_error" else fake(tmp_path, body)
    )
    journal = EventJournal(tmp_path / "state")
    result = invoke(
        repository, executable, journal, budgets=TurnBudgets(3, 16000, 128, 128, 128)
    )
    assert result.scoped_turn.observed_turn.adapter_result.process_status == status
    assert result.scoped_turn.decision.proceed is (
        not violate or status == "launch_error"
    )
    assert result.stdout.path.exists() and result.stderr.path.exists()
    assert journal.read()[-1]["event"] == "native_output_stored"
    if status != "launch_error":
        assert result.stdout.path.read_bytes() == b"[removed] out\n"


@pytest.mark.parametrize(
    "boundary", ["after_observation", "observation_append", "scope_append"]
)
def test_incomplete_f2_or_f3_failure_propagates_before_output_persistence(
    repository: Path,
    tmp_path: Path,
    boundary: str,
) -> None:
    from collections.abc import Mapping

    from agent_harness.scope import ScopeTurnError
    from agent_harness.turn import TurnObservationError

    class IncompleteJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if (
                boundary == "observation_append"
                and event["event"] == "turn_observation"
            ) or (boundary == "scope_append" and event["event"] == "scope_decision"):
                raise OSError("SENTINEL-SECRET exception")
            super().append(event)

    body = payload()
    if boundary == "after_observation":
        body += "import os\nos.mkfifo('unsupported')\n"
    journal = IncompleteJournal(tmp_path / "state")
    callbacks: list[str] = []

    def callback(text: str) -> str:
        callbacks.append(text)
        return redact(text)

    with pytest.raises(
        ScopeTurnError if boundary == "scope_append" else TurnObservationError
    ):
        invoke(repository, fake(tmp_path, body), journal, redactor=callback)
    assert not callbacks and not (tmp_path / "state" / "outputs").exists()
    assert "native_output_stored" not in journal.path.read_text()

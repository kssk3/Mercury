from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize("kind", ["item.error", "error", "turn.failed"])
@pytest.mark.parametrize(
    "message,category",
    [
        ("MCP client failed to start while handshaking", "mcp_startup"),
        ("MCP server handshake failed", "mcp_startup"),
        ("Tool unavailable", "tool_unavailable"),
        ("Authentication failed", "authentication"),
        ("Permission denied", "permission_denied"),
        ("Network connection failed", "transport_network"),
        ("Transport error", "transport_network"),
        ("Invalid configuration", "invalid_configuration"),
        ("Rate limit exceeded", "rate_limit"),
        ("Unexpected failure", "unknown"),
    ],
)
def test_error_events_keep_only_fixed_lexical_hints(
    kind: str, message: str, category: str
) -> None:
    from agent_harness.diagnostics import DiagnosticsObserver, validate_diagnostics

    payload = {"message": message + " SENTINEL-SECRET https://private/token"}
    event: dict[str, object] = (
        {"type": "item.completed", "item": {"type": "error", **payload}}
        if kind == "item.error"
        else {
            "type": kind,
            **({"error": payload} if kind == "turn.failed" else payload),
        }
    )
    observer = DiagnosticsObserver(0.0, 0.0)
    line = json.dumps(event).encode() + b"\n"
    observer.output("stdout", line[:17], 0.1)
    observer.output("stdout", line[17:], 0.2)
    result = observer.finish(1.0, 1.0)
    assert result.events[0].error_category == category
    assert result.events[0].exit_code is None
    validate_diagnostics(result)
    serialized = json.dumps(asdict(result))
    assert "SENTINEL-SECRET" not in serialized
    assert "https://private/token" not in serialized
    assert "message" not in serialized


@pytest.mark.parametrize(
    "message",
    [
        None,
        True,
        7,
        [],
        {"message": "permission denied"},
        "permission denied" + "x" * 3000,
    ],
)
def test_untrusted_message_values_do_not_create_error_hints(message: object) -> None:
    from agent_harness.diagnostics import DiagnosticsObserver

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output(
        "stdout",
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": message},
                "untrusted": {"message": "permission denied SENTINEL-SECRET"},
            }
        ).encode(),
        0.1,
    )
    result = observer.finish(1.0, 1.0)
    assert result.events[0].error_category == "unknown"
    assert "SENTINEL-SECRET" not in json.dumps(asdict(result))


def test_oversized_error_lines_are_discarded_without_losing_next_hint() -> None:
    from agent_harness.diagnostics import DiagnosticsObserver

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output("stdout", b'{"type":"error","message":"' + b"x" * 17000, 0.1)
    observer.output(
        "stdout",
        b'permission denied SENTINEL-SECRET"}\n'
        b'{"type":"error","message":"permission denied"}\n',
        0.2,
    )
    result = observer.finish(1.0, 1.0)
    assert result.oversized_lines == result.dropped_lines == 1
    assert len(result.events) == 1
    assert result.events[0].error_category == "permission_denied"
    assert "SENTINEL-SECRET" not in json.dumps(asdict(result))


@pytest.mark.parametrize(
    "kind,item_kind,status,exit_code,expected",
    [
        ("item.completed", "command_execution", "completed", 0, 0),
        ("item.completed", "command_execution", "failed", 7, 7),
        ("item.completed", "command_execution", None, -9, -9),
        ("item.completed", "command_execution", "completed", None, None),
        ("item.completed", "command_execution", "completed", True, None),
        ("item.completed", "command_execution", "completed", 0.0, None),
        ("item.completed", "command_execution", "completed", "0", None),
        ("item.started", "command_execution", "in_progress", 0, None),
        ("item.updated", "command_execution", "completed", 0, None),
        ("item.completed", "file_change", "failed", 0, None),
        ("item.completed", "mcp_tool_call", "completed", 0, None),
    ],
)
def test_only_terminal_command_events_retain_integer_exit_codes(
    kind: str,
    item_kind: str,
    status: str | None,
    exit_code: object,
    expected: int | None,
) -> None:
    from agent_harness.diagnostics import DiagnosticsObserver, validate_diagnostics

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output(
        "stdout",
        json.dumps(
            {
                "type": kind,
                "item": {
                    "type": item_kind,
                    "status": status,
                    "exit_code": exit_code,
                    "message": "permission denied SENTINEL-SECRET",
                },
            }
        ).encode(),
        0.1,
    )
    result = observer.finish(1.0, 1.0)
    assert result.events[0].exit_code == expected
    assert result.events[0].error_category is None
    assert result.events[0].status == status
    validate_diagnostics(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "error", "item_kind": None, "error_category": "SENTINEL-SECRET"},
        {"event_type": "error", "item_kind": None, "error_category": True},
        {"error_category": "permission_denied"},
        {"exit_code": True},
        {"exit_code": 1.5},
        {"exit_code": "0"},
        {"event_type": "item.started", "exit_code": 0},
        {"event_type": "item.updated", "exit_code": 0},
        {"item_kind": "file_change", "exit_code": 0},
        {"item_kind": "error", "exit_code": 0},
        {"event_type": "error", "item_kind": None, "exit_code": 0},
        {"event_type": "turn.failed", "item_kind": None, "exit_code": 0},
    ],
)
def test_custom_diagnostic_hints_and_exits_reject_invalid_associations(
    changes: dict[str, Any],
) -> None:
    from agent_harness.diagnostics import (
        DiagnosticsObserver,
        NativeEvent,
        validate_diagnostics,
    )

    result = DiagnosticsObserver(0.0, 0.0).finish(1.0, 1.0)
    event = replace(NativeEvent(0.1, "item.completed", "command_execution"), **changes)
    with pytest.raises(ValueError, match="invalid_agent_result"):
        validate_diagnostics(replace(result, events=(event,)))


def test_missing_events_and_optional_fields_remain_unknown() -> None:
    from agent_harness.diagnostics import (
        DiagnosticsObserver,
        NativeEvent,
        validate_diagnostics,
    )

    result = DiagnosticsObserver(0.0, 0.0).finish(1.0, 1.0)
    assert result.events == ()
    event = NativeEvent(0.1, "item.completed", "command_execution")
    assert event.status is None
    assert event.exit_code is None
    assert event.error_category is None
    validate_diagnostics(replace(result, events=(event,)))


@pytest.mark.parametrize(
    "kind,item_kind,text,expected",
    [
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            "command",
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=file_edit",
            "file_edit",
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=both",
            "both",
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=unknown",
            "unknown",
        ),
        (
            "item.completed",
            "agent_message",
            "SENTINEL-SECRET\nMERCURY_DIAGNOSTIC_TOOL_LIMITATION=command\nresponse",
            "command",
        ),
        ("item.completed", "agent_message", None, None),
        (
            "item.completed",
            "agent_message",
            {"text": "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command"},
            None,
        ),
        (
            "item.completed",
            "agent_message",
            "prefix MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            None,
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=arbitrary",
            None,
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command\nMERCURY_DIAGNOSTIC_TOOL_LIMITATION=file_edit",
            None,
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command\nMERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            None,
        ),
        (
            "item.completed",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command" + "x" * 3000,
            None,
        ),
        (
            "item.started",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            None,
        ),
        (
            "item.updated",
            "agent_message",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            None,
        ),
        (
            "item.completed",
            "command_execution",
            "MERCURY_DIAGNOSTIC_TOOL_LIMITATION=command",
            None,
        ),
    ],
)
def test_reported_tool_limitation_is_a_bounded_singleton_self_report(
    kind: str, item_kind: str, text: object, expected: str | None
) -> None:
    from agent_harness.diagnostics import DiagnosticsObserver, validate_diagnostics

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output(
        "stdout",
        json.dumps({"type": kind, "item": {"type": item_kind, "text": text}}).encode(),
        0.1,
    )
    result = observer.finish(1.0, 1.0)
    assert result.events[0].reported_tool_limitation == expected
    validate_diagnostics(result)
    assert "SENTINEL-SECRET" not in json.dumps(asdict(result))


@pytest.mark.parametrize(
    "kind,item_kind,limitation",
    [
        ("item.completed", "agent_message", "arbitrary"),
        ("item.completed", "agent_message", True),
        ("item.started", "agent_message", "command"),
        ("item.updated", "agent_message", "command"),
        ("item.completed", "command_execution", "command"),
        ("error", None, "command"),
    ],
)
def test_custom_reported_tool_limitation_rejects_invalid_associations(
    kind: str, item_kind: str | None, limitation: Any
) -> None:
    from agent_harness.diagnostics import (
        DiagnosticsObserver,
        NativeEvent,
        validate_diagnostics,
    )

    result = DiagnosticsObserver(0.0, 0.0).finish(1.0, 1.0)
    event = replace(
        NativeEvent(0.1, kind, item_kind), reported_tool_limitation=limitation
    )
    with pytest.raises(ValueError, match="invalid_agent_result"):
        validate_diagnostics(replace(result, events=(event,)))


def test_new_fields_persist_through_synthetic_native_output_consumer(
    tmp_path: Path,
) -> None:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import build_context_packet
    from agent_harness.contract import TaskContract
    from agent_harness.journal import EventJournal
    from agent_harness.native_output import run_stored_scoped_turn

    root = (tmp_path / "repository").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
    (root / "base.txt").write_text("baseline")
    executable = tmp_path / "synthetic-native"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdin.buffer.read()\n"
        'print(\'{"type":"error","message":"permission denied SENTINEL-SECRET"}\')\n'
        'print(\'{"type":"item.completed","item":{"type":"command_execution","status":"failed","exit_code":7,"command":"SENTINEL-SECRET"}}\')\n'
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"MERCURY_DIAGNOSTIC_TOOL_LIMITATION=both"}}\')\n'
    )
    executable.chmod(0o700)
    task = TaskContract("Fixture", ("base.txt",), (), ("Separate verification",))
    journal = EventJournal(tmp_path / "state")
    result = run_stored_scoped_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-02T00:00:00Z"),
        build_context_packet(root, source_paths=["base.txt"], budget_bytes=4096),
        journal=journal,
        budgets=TurnBudgets(3, 16000, 4096, 128, 128),
        redactor=lambda text: "[captured output omitted]",
        max_output_bytes=128,
    )
    observation = next(
        event for event in journal.read() if event["event"] == "turn_observation"
    )
    diagnostics = observation["diagnostics"]
    assert isinstance(diagnostics, dict)
    events = diagnostics["events"]
    assert events[0]["error_category"] == "permission_denied"
    assert events[1]["exit_code"] == 7
    assert events[1]["status"] == "failed"
    assert events[2]["reported_tool_limitation"] == "both"
    assert result.stdout.path.read_text() == "[captured output omitted]"
    assert "SENTINEL-SECRET" not in journal.path.read_text()


def test_stream_metadata_is_bounded_sanitized_and_chunk_independent() -> None:
    from agent_harness.diagnostics import DiagnosticsObserver

    observer = DiagnosticsObserver(10.0, 100.0)
    line = json.dumps(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "status": "in_progress",
                "command": "SECRET-COMMAND /private/path",
                "id": "SECRET-ID",
            },
            "thread_id": "SECRET-THREAD",
        }
    ).encode()
    observer.output("stdout", line[:11], 0.1)
    observer.output("stdout", line[11:] + b"\n", 0.2)
    observer.output("stderr", b"SECRET-STDERR", 0.3)
    observer.output("stdout", b"\xff\n{broken}\n", 0.4)
    observer.output("stdout", b"x" * 20000, 0.5)
    observer.output("stdout", b"x" * 20000 + b"\n", 0.6)
    observer.output("stdout", b'{"type":"unknown.SECRET"}\n', 0.7)
    observer.output(
        "stdout",
        b'{"type":"item.started","item":{"type":"SECRET-tool"}}\n',
        0.8,
    )
    for _ in range(40):
        observer.output("stdout", b'{"type":"turn.started"}\n', 0.9)
    observer.output("stdout", b'{"type":"turn.completed"}', 1.0)
    observer.eof("stdout", 1.1)
    result = observer.finish(2.0, 102.0)
    assert len(result.events) == 32
    assert result.dropped_lines == 5
    assert result.oversized_lines == 1
    assert result.dropped_events == 10
    assert result.events_truncated
    assert result.events[0].event_type == "item.started"
    assert result.events[0].item_kind == "command_execution"
    assert result.events[0].status == "in_progress"
    assert result.milestones.stdout_first == 0.1
    assert result.milestones.stdout_last == 1.0
    assert result.milestones.stdout_eof == 1.1
    assert result.milestones.stderr_eof is None
    assert "SECRET" not in json.dumps(asdict(result))
    assert "private" not in json.dumps(asdict(result))


def test_final_unterminated_line_and_duplicate_fields_are_handled() -> None:
    from agent_harness.diagnostics import DiagnosticsObserver

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output("stdout", b'{"type":"turn.started","type":"turn.completed"}\n', 0.1)
    observer.output(
        "stdout", b'{"type":"turn.failed","error":{"message":"SECRET"}}', 0.2
    )
    observer.eof("stdout", 0.3)
    result = observer.finish(0.4, 0.4)
    assert [event.event_type for event in result.events] == ["turn.failed"]
    assert result.dropped_lines == 1
    assert "SECRET" not in json.dumps(asdict(result))
    incomplete = DiagnosticsObserver(0.0, 0.0)
    incomplete.output("stdout", b'{"type":"turn.started"}', 0.1)
    retained = incomplete.finish(0.2, 0.2)
    assert retained.milestones.stdout_eof is None
    assert [event.event_type for event in retained.events] == ["turn.started"]


@pytest.mark.parametrize(
    "elapsed,wall,status,discrepancy",
    [
        (5.0, 5.5, "consistent", 0.5),
        (5.0, 6.0, "consistent", 1.0),
        (5.0, 8.0, "discrepant", 3.0),
        (5.0, -2.0, "discrepant", -7.0),
        (None, 5.0, "unknown", None),
        (float("nan"), 5.0, "unknown", None),
        (5.0, float("inf"), "unknown", None),
        (-1.0, 5.0, "unknown", None),
    ],
)
def test_clock_reports_signed_discrepancy_without_timeout_decision(
    elapsed: float | None, wall: float, status: str, discrepancy: float | None
) -> None:
    from agent_harness.diagnostics import observe_clock

    clock = observe_clock(elapsed, wall)
    assert clock.status == status
    assert clock.discrepancy_seconds == discrepancy
    assert clock.tolerance_seconds == 1.0


@pytest.mark.parametrize(
    "malformation", ["kind", "status", "numeric", "counter", "clock"]
)
def test_custom_result_diagnostics_are_validated_before_persistence(
    tmp_path: Path, malformation: str
) -> None:
    from agent_harness.agent import (
        AgentTurnResult,
        CapturedText,
        TurnBudgets,
        _validate_result,
    )
    from agent_harness.diagnostics import DiagnosticsObserver

    observer = DiagnosticsObserver(0.0, 0.0)
    observer.output(
        "stdout", b'{"type":"item.started","item":{"type":"command_execution"}}\n', 0.1
    )
    diagnostics = observer.finish(1.0, 1.0)
    if malformation == "kind":
        diagnostics = replace(
            diagnostics, events=(replace(diagnostics.events[0], item_kind="SECRET"),)
        )
    elif malformation == "status":

        class HostileString(str):
            pass

        diagnostics = replace(
            diagnostics,
            events=(replace(diagnostics.events[0], status=HostileString("completed")),),
        )
    elif malformation == "numeric":
        diagnostics = replace(
            diagnostics,
            milestones=replace(diagnostics.milestones, stdout_first=float("nan")),
        )
    elif malformation == "counter":
        diagnostics = replace(diagnostics, dropped_events=True)
    else:
        diagnostics = replace(
            diagnostics,
            clock=replace(
                diagnostics.clock, status="consistent", discrepancy_seconds=9.0
            ),
        )
    capture = CapturedText("", 0, False)
    result = AgentTurnResult(
        (),
        str(tmp_path),
        "successful_exit",
        0,
        1.0,
        capture,
        capture,
        None,
        "missing",
        diagnostics,
    )
    with pytest.raises(ValueError, match="invalid_agent_result"):
        _validate_result(result, tmp_path, TurnBudgets(2, 100, 100, 100, 100))

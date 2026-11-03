"""Bounded observations, never native trust, task success or model latency."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, replace

_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.updated",
        "item.completed",
        "error",
    }
)
_ITEM_KINDS = frozenset(
    {
        "agent_message",
        "reasoning",
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "web_search",
        "todo_list",
        "error",
        "collab_tool_call",
    }
)
_STATUSES = frozenset({"in_progress", "completed", "failed"})
_ERROR_PHRASES = (
    ("tool_unavailable", ("tool unavailable", "tool not found", "unknown tool")),
    (
        "authentication",
        ("authentication failed", "authentication required", "unauthorized"),
    ),
    ("permission_denied", ("permission denied", "access denied")),
    (
        "transport_network",
        ("transport error", "network connection failed", "connection refused"),
    ),
    ("invalid_configuration", ("invalid configuration", "configuration error")),
    ("rate_limit", ("rate limit", "rate_limit_exceeded")),
)
_ERROR_CATEGORIES = frozenset(
    {"unknown", "mcp_startup", *(category for category, _ in _ERROR_PHRASES)}
)
_MESSAGE_CHARS = 2048
_TOOL_LIMITATIONS = frozenset({"command", "file_edit", "both", "unknown"})
_TOOL_LIMITATION_MARKER = "MERCURY_DIAGNOSTIC_TOOL_LIMITATION="
_LINE_BYTES = 16384
_EVENT_LIMIT = 32
_CLOCK_TOLERANCE = 1.0


@dataclass(frozen=True)
class ExecutionMilestones:
    """Elapsed seconds from launch start; null means not observed."""

    launch_started: float | None = 0.0
    process_started: float | None = None
    stdin_complete: float | None = None
    stdin_incomplete: float | None = None
    stdout_first: float | None = None
    stdout_last: float | None = None
    stderr_first: float | None = None
    stderr_last: float | None = None
    stdout_eof: float | None = None
    stderr_eof: float | None = None
    process_exit: float | None = None
    timeout: float | None = None
    cleanup_started: float | None = None
    cleanup_finished: float | None = None


@dataclass(frozen=True)
class ClockObservation:
    monotonic_elapsed_seconds: float | None
    wall_span_seconds: float | None
    discrepancy_seconds: float | None
    status: str
    tolerance_seconds: float = _CLOCK_TOLERANCE


@dataclass(frozen=True)
class NativeEvent:
    elapsed_seconds: float
    event_type: str
    item_kind: str | None = None
    status: str | None = None
    error_category: str | None = None
    exit_code: int | None = None
    reported_tool_limitation: str | None = None


@dataclass(frozen=True)
class ExecutionDiagnostics:
    milestones: ExecutionMilestones
    clock: ClockObservation
    events: tuple[NativeEvent, ...] = ()
    dropped_lines: int = 0
    oversized_lines: int = 0
    dropped_events: int = 0
    events_truncated: bool = False


def _number(value: object, *, signed: bool = False) -> bool:
    if not isinstance(value, (int, float)) or type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and (signed or value >= 0)
    except OverflowError:
        return False


def observe_clock(elapsed: float | None, wall_span: float | None) -> ClockObservation:
    """Wall minus monotonic; invalid spans cannot establish consistency."""
    elapsed = elapsed if _number(elapsed) else None
    wall_span = wall_span if _number(wall_span, signed=True) else None
    discrepancy = (
        wall_span - elapsed if elapsed is not None and wall_span is not None else None
    )
    if discrepancy is not None and not math.isfinite(discrepancy):
        discrepancy = None
    status = (
        "unknown"
        if discrepancy is None
        else "consistent"
        if abs(discrepancy) <= _CLOCK_TOLERANCE
        else "discrepant"
    )
    return ClockObservation(elapsed, wall_span, discrepancy, status)


def validate_diagnostics(value: object) -> None:
    """Validate even custom backend records before dataclass serialization."""
    if type(value) is not ExecutionDiagnostics:
        raise ValueError("invalid_agent_result")
    if (
        type(value.milestones) is not ExecutionMilestones
        or type(value.clock) is not ClockObservation
    ):
        raise ValueError("invalid_agent_result")
    if any(
        elapsed is not None and not _number(elapsed)
        for field in fields(ExecutionMilestones)
        for elapsed in (getattr(value.milestones, field.name),)
    ):
        raise ValueError("invalid_agent_result")
    clock = value.clock
    if (
        type(clock.status) is not str
        or type(clock.tolerance_seconds) not in (float, int)
        or clock.tolerance_seconds != _CLOCK_TOLERANCE
        or any(
            span is not None and not _number(span, signed=signed)
            for span, signed in (
                (clock.monotonic_elapsed_seconds, False),
                (clock.wall_span_seconds, True),
                (clock.discrepancy_seconds, True),
            )
        )
        or clock
        != observe_clock(clock.monotonic_elapsed_seconds, clock.wall_span_seconds)
    ):
        raise ValueError("invalid_agent_result")
    if (
        type(value.events) is not tuple
        or len(value.events) > _EVENT_LIMIT
        or type(value.events_truncated) is not bool
        or any(
            type(count) is not int or count < 0
            for count in (
                value.dropped_lines,
                value.oversized_lines,
                value.dropped_events,
            )
        )
        or value.events_truncated != (value.dropped_events > 0)
    ):
        raise ValueError("invalid_agent_result")
    for event in value.events:
        if (
            type(event) is not NativeEvent
            or not _number(event.elapsed_seconds)
            or type(event.event_type) is not str
            or event.event_type not in _EVENT_TYPES
            or (
                event.item_kind is not None
                and (
                    type(event.item_kind) is not str
                    or event.item_kind not in _ITEM_KINDS
                )
            )
            or (
                event.status is not None
                and (type(event.status) is not str or event.status not in _STATUSES)
            )
            or (event.event_type.startswith("item.") != (event.item_kind is not None))
            or (event.item_kind is None and event.status is not None)
            or (
                event.error_category is not None
                and (
                    type(event.error_category) is not str
                    or event.error_category not in _ERROR_CATEGORIES
                    or not _error_event(event.event_type, event.item_kind)
                )
            )
            or (
                event.exit_code is not None
                and (
                    type(event.exit_code) is not int
                    or event.event_type != "item.completed"
                    or event.item_kind != "command_execution"
                )
            )
            or (
                event.reported_tool_limitation is not None
                and (
                    type(event.reported_tool_limitation) is not str
                    or event.reported_tool_limitation not in _TOOL_LIMITATIONS
                    or event.event_type != "item.completed"
                    or event.item_kind != "agent_message"
                )
            )
        ):
            raise ValueError("invalid_agent_result")


def _error_event(event_type: str, item_kind: str | None) -> bool:
    return event_type in {"error", "turn.failed"} or item_kind == "error"


def _error_category(message: object) -> str:
    """Fixed lexical hint only, never a verified cause or retained native text."""
    if type(message) is not str or len(message) > _MESSAGE_CHARS:
        return "unknown"
    text = message.casefold()
    if "mcp" in text and any(
        phrase in text
        for phrase in ("failed to start", "startup", "handshake", "handshaking")
    ):
        return "mcp_startup"
    for category, phrases in _ERROR_PHRASES:
        if any(phrase in text for phrase in phrases):
            return category
    return "unknown"


def _reported_tool_limitation(text: object) -> str | None:
    """Bounded untrusted self-report; absence never establishes availability."""
    if type(text) is not str or len(text) > _MESSAGE_CHARS:
        return None
    markers = [
        line.removeprefix(_TOOL_LIMITATION_MARKER)
        for line in text.splitlines()
        if line.startswith(_TOOL_LIMITATION_MARKER)
    ]
    if len(markers) == 1 and markers[0] in _TOOL_LIMITATIONS:
        return markers[0]
    return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class DiagnosticsObserver:
    """One Codex invocation; feed all stdout independently of raw capture caps."""

    def __init__(self, started: float, wall_started: float) -> None:
        self.started = started
        self.wall_started = wall_started
        self._milestones = ExecutionMilestones()
        self._pending = bytearray()
        self._discarding = False
        self._events: list[NativeEvent] = []
        self._dropped_lines = 0
        self._oversized_lines = 0
        self._dropped_events = 0

    def mark(self, name: str, elapsed: float) -> None:
        if getattr(self._milestones, name) is None:
            self._milestones = replace(self._milestones, **{name: elapsed})

    def output(self, stream: str, chunk: bytes, elapsed: float) -> None:
        self.mark(stream + "_first", elapsed)
        self._milestones = replace(self._milestones, **{stream + "_last": elapsed})
        if stream != "stdout":
            return
        # Never split an entire unbounded stream or retain an oversized line.
        offset = 0
        while offset < len(chunk):
            newline = chunk.find(b"\n", offset)
            end = len(chunk) if newline == -1 else newline
            size = end - offset
            if not self._discarding:
                if len(self._pending) + size > _LINE_BYTES:
                    self._pending.clear()
                    self._discarding = True
                    self._dropped_lines += 1
                    self._oversized_lines += 1
                else:
                    self._pending.extend(chunk[offset:end])
            if newline == -1:
                break
            if not self._discarding:
                self._line(bytes(self._pending), elapsed)
            self._pending.clear()
            self._discarding = False
            offset = newline + 1

    def eof(self, stream: str, elapsed: float) -> None:
        self.mark(stream + "_eof", elapsed)
        if stream == "stdout":
            if self._pending and not self._discarding:
                self._line(bytes(self._pending), elapsed)
            self._pending.clear()
            self._discarding = False

    def _line(self, raw: bytes, elapsed: float) -> None:
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(data, dict):
                raise ValueError("invalid event")
            kind = data.get("type")
            if type(kind) is not str or kind not in _EVENT_TYPES:
                raise ValueError("unknown event")
            item_kind = status = None
            exit_code = error_category = None
            reported_tool_limitation = None
            if kind.startswith("item."):
                item = data.get("item")
                if not isinstance(item, dict):
                    raise ValueError("invalid item")
                item_kind = item.get("type")
                status = item.get("status")
                if type(item_kind) is not str or item_kind not in _ITEM_KINDS:
                    raise ValueError("unknown item")
                if status is not None and (
                    type(status) is not str or status not in _STATUSES
                ):
                    raise ValueError("unknown status")
                if item_kind == "error":
                    error_category = _error_category(item.get("message"))
                if kind == "item.completed" and item_kind == "command_execution":
                    code = item.get("exit_code")
                    if type(code) is int:
                        exit_code = code
                if kind == "item.completed" and item_kind == "agent_message":
                    reported_tool_limitation = _reported_tool_limitation(
                        item.get("text")
                    )
            elif kind == "error":
                error_category = _error_category(data.get("message"))
            elif kind == "turn.failed":
                error = data.get("error")
                error_category = _error_category(
                    error.get("message") if isinstance(error, dict) else None
                )
            event = NativeEvent(
                elapsed,
                kind,
                item_kind,
                status,
                error_category,
                exit_code,
                reported_tool_limitation,
            )
        except (ValueError, UnicodeError, RecursionError):
            self._dropped_lines += 1
            return
        if len(self._events) == _EVENT_LIMIT:
            self._dropped_events += 1
        else:
            self._events.append(event)

    def finish(self, elapsed: float, wall_ended: float) -> ExecutionDiagnostics:
        # Forced closure is not observed EOF. Parse a bounded final fragment
        # without claiming that the native stream actually closed.
        if self._pending and not self._discarding:
            self._line(bytes(self._pending), elapsed)
        self._pending.clear()
        return ExecutionDiagnostics(
            self._milestones,
            observe_clock(elapsed, wall_ended - self.wall_started),
            tuple(self._events),
            self._dropped_lines,
            self._oversized_lines,
            self._dropped_events,
            self._dropped_events > 0,
        )

"""The HTML view presents one explanation snapshot without acquiring new facts."""

from __future__ import annotations

import json
import socket
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from test_explanation import bound_config

from agent_harness.explanation import explain_execution


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.snapshot: list[str] = []
        self.in_snapshot = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "pre" and "data-snapshot" in attributes:
            self.in_snapshot = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self.in_snapshot = False

    def handle_data(self, data: str) -> None:
        if self.in_snapshot:
            self.snapshot.append(data)


def test_html_preserves_the_exact_snapshot_as_escaped_data(tmp_path: Path) -> None:
    from agent_harness.html_report import render_execution_html

    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    original = json.dumps(snapshot, sort_keys=True)
    report = render_execution_html(snapshot)
    parsed = ReportParser()
    parsed.feed(report)
    assert json.loads("".join(parsed.snapshot)) == snapshot
    assert json.dumps(snapshot, sort_keys=True) == original
    assert '<html lang="ko">' in report
    assert "과거 기록" in report
    assert "현재 작업 공간" in report
    assert "수동 승인" in report
    assert "자동 연결" in report
    assert "KST" in report


def parse_report(report: str) -> tuple[ReportParser, dict[str, Any]]:
    parsed = ReportParser()
    parsed.feed(report)
    return parsed, json.loads("".join(parsed.snapshot))


@pytest.mark.parametrize(
    "mode", ["bound", "stale", "missing_config", "missing_state", "corrupt"]
)
def test_html_does_not_promote_or_hide_snapshot_states(
    tmp_path: Path, mode: str
) -> None:
    from agent_harness.html_report import render_execution_html

    config = bound_config(tmp_path)
    kwargs: dict[str, Any] = {"config_path": config}
    if mode == "stale":
        kwargs["expected_revision"] = "f" * 40
    elif mode == "missing_config":
        kwargs = {}
    elif mode == "missing_state":
        (tmp_path / "current.json").unlink()
    elif mode == "corrupt":
        (tmp_path / "current.json").write_text("{")
    snapshot: Any = explain_execution(tmp_path, **kwargs)
    report = render_execution_html(snapshot)
    _, decoded = parse_report(report)
    assert decoded == snapshot
    assert snapshot["explanation"]["conclusion"] in report
    assert snapshot["explanation"]["evidence_readiness"] in report
    assert snapshot["explanation"]["approval"]["state"] in report
    assert snapshot["explanation"]["review"]["state"] in report
    assert all(reason in report for reason in snapshot["explanation"]["blockers"])


def test_hostile_snapshot_text_cannot_become_markup_or_resources(
    tmp_path: Path,
) -> None:
    from agent_harness.html_report import render_execution_html

    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    hostile = '</pre><script>alert(1)</script><img src="https://invalid.example/" onerror="boom">&'
    snapshot["recorded_reason"] = hostile
    snapshot["explanation"]["criteria"][0]["criterion"] = hostile
    snapshot[hostile] = hostile
    report = render_execution_html(snapshot)
    parsed, decoded = parse_report(report)
    assert decoded == snapshot
    assert not (
        {"script", "img", "iframe", "form", "button", "link"}
        & {tag for tag, _ in parsed.tags}
    )
    assert all(not key.startswith("on") for _, attrs in parsed.tags for key in attrs)
    policy = next(
        attrs["content"]
        for tag, attrs in parsed.tags
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert policy is not None
    assert (
        "default-src 'none'" in policy
        and "script-src 'none'" in policy
        and "connect-src 'none'" in policy
    )
    assert hostile not in report


def test_pure_renderer_has_no_io_and_uses_saved_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.html_report import render_execution_html

    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    snapshot["explanation"]["observed_at"] = "2026-11-03T01:02:03+00:00"
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("pure rendering acquired facts or caused a side effect")

    with monkeypatch.context() as guard:
        for method in ("read_text", "read_bytes", "write_text", "write_bytes", "open"):
            guard.setattr(Path, method, forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(socket, "socket", forbidden)
        report = render_execution_html(snapshot)
    assert "2026-11-03 10:02:03 KST" in report
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}


def test_html_cli_renders_one_exact_explanation_and_preserves_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_harness import explanation
    from agent_harness.cli import main

    config = bound_config(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    original = explanation.explain_execution
    snapshots: list[dict[str, object]] = []

    def capture(*args: Any, **kwargs: Any) -> dict[str, object]:
        snapshot = original(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("HTML inspection started execution or networking")

    monkeypatch.setattr(explanation, "explain_execution", capture)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert (
        main(
            [
                "status",
                "--state-dir",
                str(tmp_path),
                "--explain",
                "--config",
                str(config),
                "--format",
                "html",
            ]
        )
        == 0
    )
    _, decoded = parse_report(capsys.readouterr().out)
    assert len(snapshots) == 1 and decoded == snapshots[0]
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_html_cli_unknown_errors_keep_exit_one(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_harness.cli import main

    if mode == "corrupt":
        bound_config(tmp_path)
        (tmp_path / "current.json").write_text("{")
    assert (
        main(["status", "--state-dir", str(tmp_path), "--explain", "--format", "html"])
        == 1
    )
    _, decoded = parse_report(capsys.readouterr().out)
    assert decoded["recorded_status"] == "unknown" and decoded["diagnostics"]


@pytest.mark.parametrize(
    "options", [["--format", "html"], ["--explain", "--format", "pdf"]]
)
def test_html_cli_syntax_error_requires_explicit_explanation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], options: list[str]
) -> None:
    from agent_harness.cli import main

    with pytest.raises(SystemExit) as error:
        main(["status", "--state-dir", str(tmp_path), *options])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_explicit_json_and_default_status_remain_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_harness.cli import main

    bound_config(tmp_path)
    assert main(["status", "--state-dir", str(tmp_path)]) == 0
    default = capsys.readouterr().out
    assert main(["status", "--state-dir", str(tmp_path), "--format", "json"]) == 0
    assert capsys.readouterr().out == default


def test_verified_runtime_imports_new_renderer_and_preserves_unknown_result(
    tmp_path: Path,
) -> None:
    import shutil
    from importlib.metadata import distribution

    from test_verified_runtime import SCRIPT, bundle, digest, dispatch

    root, manifest, _ = bundle(tmp_path)
    shutil.rmtree(root / "src")
    shutil.copytree(
        SCRIPT.parents[1] / "src",
        root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "AGENTS.md"),
    )
    installed = distribution("agent-harness")
    metadata = (
        root / "src" / f"agent_harness-{installed.version}.dist-info" / "METADATA"
    )
    metadata.parent.mkdir()
    metadata.write_text(installed.read_text("METADATA") or "")
    frozen = json.loads(manifest.read_text())
    frozen["files"] = {
        str(p.relative_to(root / "src")): digest(p)
        for p in sorted((root / "src").rglob("*"))
        if p.is_file()
    }
    assert "agent_harness/html_report.py" in frozen["files"]
    manifest.write_text(json.dumps(frozen))
    state = tmp_path / "missing-state"
    result = dispatch(
        root,
        manifest,
        arguments=(
            "status",
            "--state-dir",
            str(state),
            "--explain",
            "--format",
            "html",
        ),
    )
    assert result.returncode == 1, result.stderr
    _, snapshot = parse_report(result.stdout)
    assert snapshot["recorded_status"] == "unknown"
    assert snapshot["diagnostics"] == ["missing_input"]
    assert not state.exists()

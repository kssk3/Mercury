"""Report contract: one honest snapshot, fresh bounded writes, no execution."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_explanation import bound_config, persist
from test_external_checks import collect
from test_html_report import parse_report

from agent_harness import explanation, html_report
from agent_harness.cli import main
from agent_harness.report import export_report
from agent_harness.review_evidence import _candidate, build_review_evidence


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["/usr/bin/git", "init", "-q", str(repository)], check=True, timeout=10
    )
    state = tmp_path / "state"
    state.mkdir()
    config = bound_config(state)
    return repository, state, config, tmp_path / "output"


def export(tmp_path: Path, **kwargs: Any) -> tuple[Path, Path]:
    repository, state, config, output = fixture(tmp_path)
    export_report(
        state,
        repository=repository,
        output_directory=output,
        config_path=config,
        **kwargs,
    )
    assert output.is_dir(), "report must create the fresh output bundle"
    return output, state


def test_one_capture_drives_three_lossless_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 guarantees a single capture, exact HTML/JSON and exactly three files."""
    captures: list[dict[str, object]] = []
    original = explanation.explain_execution

    def capture(*args: Any, **kwargs: Any) -> dict[str, object]:
        snapshot = original(*args, **kwargs)
        captures.append(snapshot)
        return snapshot

    monkeypatch.setattr(explanation, "explain_execution", capture)
    output, _ = export(tmp_path)
    assert {p.name for p in output.iterdir()} == {
        "explanation.json",
        "report.html",
        "record.md",
    }
    assert len(captures) == 1
    saved = json.loads((output / "explanation.json").read_text())
    assert saved == captures[0]
    _, embedded = parse_report((output / "report.html").read_text())
    assert embedded == saved
    assert (
        html_report.render_execution_html(saved) == (output / "report.html").read_text()
    )


def test_markdown_has_observed_outcome_links_and_generator_identity(
    tmp_path: Path,
) -> None:
    """R2 guarantees attributable facts, not inferred approval or delivery."""
    output, _ = export(tmp_path)
    markdown = (output / "record.md").read_text()
    saved = json.loads((output / "explanation.json").read_text())
    assert saved["recorded_status"] in markdown
    assert saved["explanation"]["evidence_readiness"] in markdown
    assert "explanation.json" in markdown and "report.html" in markdown
    assert "unavailable" in markdown.lower() or "not recorded" in markdown.lower()
    assert (
        hashlib.sha256(Path(html_report.__file__).read_bytes()).hexdigest() in markdown
    )
    assert "not published" in markdown.lower() or "unavailable" in markdown.lower()


def test_markdown_escapes_untrusted_links_and_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 opaque facts stay in JSON without becoming executable Markdown/HTML."""
    original = explanation.explain_execution
    hostile = (
        '<img src="https://invalid.example/x">\n![leak](https://invalid.example/p)'
    )

    def capture(*args: Any, **kwargs: Any) -> dict[str, object]:
        value = original(*args, **kwargs)
        value["recorded_reason"] = hostile
        return value

    monkeypatch.setattr(explanation, "explain_execution", capture)
    output, _ = export(tmp_path)
    assert (
        json.loads((output / "explanation.json").read_text())["recorded_reason"]
        == hostile
    )
    markdown = (output / "record.md").read_text()
    assert "<img " not in markdown and "![leak](" not in markdown


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_unreadable_history_exports_unknown_without_promotion(
    tmp_path: Path, mode: str
) -> None:
    """R3 generation of UNKNOWN is valid; it must never become a verification pass."""
    repository, state, config, output = fixture(tmp_path)
    if mode == "missing":
        (state / "current.json").unlink()
    else:
        (state / "current.json").write_text("{")
    export_report(
        state, repository=repository, output_directory=output, config_path=config
    )
    assert (output / "explanation.json").exists(), "UNKNOWN remains reportable"
    snapshot = json.loads((output / "explanation.json").read_text())
    assert snapshot["recorded_status"] == "unknown" and snapshot["diagnostics"]
    assert "unknown" in (output / "record.md").read_text().lower()


@pytest.mark.parametrize("mode", ["pass", "tampered"])
def test_external_receipts_keep_their_own_outcome_and_counts(
    tmp_path: Path, mode: str
) -> None:
    """R3 external PASS and invalid receipts remain separate from native UNKNOWN."""
    check_root = tmp_path / "check"
    check_root.mkdir()
    bundle, _ = collect(check_root)
    if mode == "tampered":
        (bundle / "result.xml").write_text("<changed/>")
    repository, state, _, output = fixture(tmp_path)
    (state / "current.json").unlink()
    export_report(
        state,
        repository=repository,
        output_directory=output,
        external_checks=(bundle / "receipt.json",),
    )
    assert (output / "explanation.json").exists()
    snapshot = json.loads((output / "explanation.json").read_text())
    assert snapshot["recorded_status"] == "unknown"
    row = snapshot["explanation"]["external_checks"][0]
    assert row["outcome"] == ("PASS" if mode == "pass" else "UNKNOWN")
    assert row["outcome"] in (output / "record.md").read_text()
    if mode == "pass":
        assert row["tests"]["counts"]["tests"] == 1


def test_report_never_launches_checkers_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 permits trusted Git path queries, not native/checker or network work."""
    repository, state, config, output = fixture(tmp_path)
    original = subprocess.Popen

    def guarded(*args: Any, **kwargs: Any) -> Any:
        argv = args[0] if args else kwargs["args"]
        assert Path(argv[0]).name == "git", "report attempted a non-Git subprocess"
        return original(*args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("report attempted network access")

    monkeypatch.setattr(subprocess, "Popen", guarded)
    monkeypatch.setattr(socket, "socket", forbidden)
    export_report(
        state, repository=repository, output_directory=output, config_path=config
    )
    assert output.is_dir()


@pytest.mark.parametrize(
    "mode", ["repository", "git", "protected", "state", "receipt_parent", "config"]
)
def test_rejects_output_overlapping_repository_or_inputs_before_write(
    tmp_path: Path, mode: str
) -> None:
    """R5 rejects supplied root/subdir, metadata and both input/protected boundaries."""
    repository, state, config, output = fixture(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    receipt_parent = tmp_path / "external"
    receipt_parent.mkdir()
    destinations = {
        "repository": repository / "output",
        "git": repository / ".git/output",
        "protected": protected / "output",
        "state": state / "output",
        "receipt_parent": receipt_parent / "output",
        "config": config,
    }
    output = destinations[mode]
    subdir = repository / "subdir"
    subdir.mkdir()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, OSError)):
        export_report(
            state,
            repository=subdir,
            output_directory=output,
            config_path=config,
            external_checks=(receipt_parent / "receipt.json",),
            protected_paths=(protected,),
        )
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "mode", ["existing", "symlink", "missing_parent", "file_parent", "ancestor"]
)
def test_rejects_unsafe_or_nonfresh_output_selection(tmp_path: Path, mode: str) -> None:
    """R5 refuses existing/aliased outputs and ancestor overlap without cleanup."""
    repository, state, config, output = fixture(tmp_path)
    kwargs: dict[str, Any] = {}
    if mode == "existing":
        output.mkdir()
        (output / "keep").write_text("original")
    elif mode == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(state, target_is_directory=True)
        output = alias / "new"
    elif mode == "missing_parent":
        output = tmp_path / "absent/new"
    elif mode == "file_parent":
        parent = tmp_path / "file"
        parent.write_text("original")
        output = parent / "new"
    else:
        kwargs["protected_paths"] = (output / "protected-child",)
    with pytest.raises((ValueError, OSError)):
        export_report(
            state,
            repository=repository,
            output_directory=output,
            config_path=config,
            **kwargs,
        )
    if mode == "existing":
        assert (output / "keep").read_text() == "original"


def test_rejects_relocated_actual_git_metadata(tmp_path: Path) -> None:
    """R5 actual Git metadata is protected even outside the working tree."""
    root = tmp_path / "worktree"
    metadata = tmp_path / "actual-metadata"
    subprocess.run(
        ["/usr/bin/git", "init", "-q", "--separate-git-dir", str(metadata), str(root)],
        check=True,
        timeout=10,
    )
    state = tmp_path / "state"
    state.mkdir()
    bound_config(state)
    output = metadata / "report"
    with pytest.raises((ValueError, OSError)):
        export_report(state, repository=root, output_directory=output)
    assert not output.exists()


def test_partial_write_failure_preserves_originals_and_completed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6 failed later persistence raises, preserves earlier bytes and never retries."""
    repository, state, config, output = fixture(tmp_path)
    current_note = tmp_path / "current.md"
    current_note.write_text("keep this current record")
    before = {p: p.read_bytes() for p in state.iterdir()}
    original_open = Path.open
    completed: list[Path] = []

    def controlled(path: Path, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.parent == output and any(flag in mode for flag in ("x", "w", "a", "+")):
            if completed:
                raise OSError("synthetic second-artifact failure")
            completed.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", controlled)
    with pytest.raises((ValueError, OSError)):
        export_report(
            state,
            repository=repository,
            output_directory=output,
            config_path=config,
            protected_paths=(current_note,),
        )
    assert len(completed) == 1 and completed[0].exists()
    assert len(list(output.iterdir())) == 1
    assert current_note.read_text() == "keep this current record"
    assert before == {p: p.read_bytes() for p in state.iterdir()}


@pytest.mark.parametrize(
    "mode,expected", [("readable_fail", 0), ("missing", 1), ("existing_output", 2)]
)
def test_cli_generation_codes_are_not_assertion_pass_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str, expected: int
) -> None:
    """R3/R7 report CLI distinguishes readable failure, unknown and write refusal."""
    repository, state, config, output = fixture(tmp_path)
    if mode == "missing":
        (state / "current.json").unlink()
    elif mode == "existing_output":
        output.mkdir()
    else:
        value = json.loads((state / "current.json").read_text())
        events = [
            json.loads(line)
            for line in (state / "events.jsonl").read_text().splitlines()
        ]
        for record in [
            *events,
            value["attempt_summary"]["active_attempt"]["verification"],
        ]:
            if record.get("event") == "attempt_verification":
                record["commands"][0].update(exit_code=1, timed_out=False)
                record.update(technical_kind="fail", overall_kind="fail")
                saved = record["review_binding"]
                record["review_binding"] = build_review_evidence(
                    record, _candidate(saved["candidate"]), saved["verification_sha256"]
                )
        value["loop_summary"]["status"] = "fail"
        events[-1]["status"] = "fail"
        persist(state, value, events)
        assert not explanation.explain_execution(state, config_path=config)[
            "diagnostics"
        ]
    code: int | str | None
    try:
        code = main(
            [
                "report",
                "--state-dir",
                str(state),
                "--repository",
                str(repository),
                "--output-dir",
                str(output),
                "--config",
                str(config),
            ]
        )
    except SystemExit as error:
        code = error.code
    assert code == expected
    captured = capsys.readouterr()
    assert "invalid choice" not in captured.err
    if expected == 2:
        assert captured.out == "" and captured.err
    else:
        assert (output / "record.md").exists()
        assert isinstance(json.loads(captured.out), dict)


def test_rejects_protected_symlink_parent_traversal_before_writing(
    tmp_path: Path,
) -> None:
    """R5 protects the actually selected location, including symlink/.. aliases."""
    root = tmp_path.resolve()
    repository, state, config, _ = fixture(root)
    (root / "real/deep").mkdir(parents=True)
    protected = root / "real/protected"
    protected.mkdir()
    sentinel = protected / "keep"
    sentinel.write_text("original protected bytes")
    (root / "alias").symlink_to(root / "real/deep", target_is_directory=True)
    selected = root / "alias/../protected"
    assert selected.resolve() == protected
    output = protected / "report"

    with pytest.raises((ValueError, OSError)):
        export_report(
            state,
            repository=repository,
            output_directory=output,
            config_path=config,
            protected_paths=(selected,),
        )

    assert not output.exists()
    assert sentinel.read_text() == "original protected bytes"


def test_rejects_output_inside_project_selected_by_terminal_alias(
    tmp_path: Path,
) -> None:
    """RC-01/R5 selected project B stays protected when its alias lives in A."""
    root = tmp_path.resolve()
    repository_a, state, config, _ = fixture(root)
    repository_b = root / "project-b"
    repository_b.mkdir()
    subprocess.run(
        ["/usr/bin/git", "init", "-q", str(repository_b)], check=True, timeout=10
    )
    sentinel = repository_b / "keep"
    sentinel.write_text("original project-b bytes")
    selected = repository_a / "selected-project"
    selected.symlink_to(repository_b, target_is_directory=True)
    assert selected.resolve() == repository_b
    output = repository_b / "report"

    with pytest.raises((ValueError, OSError)):
        export_report(
            state,
            repository=selected,
            output_directory=output,
            config_path=config,
        )

    assert not output.exists()
    assert sentinel.read_text() == "original project-b bytes"


@pytest.mark.parametrize(
    "opaque",
    [
        "see https://invalid.example/path",
        "see www.invalid.example/path",
        "contact user@invalid.example",
        "see `https://invalid.example/path` and ``recorded marker``",
    ],
)
def test_opaque_recorded_reason_is_literal_markdown_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opaque: str
) -> None:
    """RC-02/R2 non-identifiers are literal code spans; exact source stays JSON."""
    original = explanation.explain_execution

    def capture(*args: Any, **kwargs: Any) -> dict[str, object]:
        snapshot = original(*args, **kwargs)
        snapshot["recorded_reason"] = opaque
        return snapshot

    monkeypatch.setattr(explanation, "explain_execution", capture)
    output, _ = export(tmp_path)
    snapshot = json.loads((output / "explanation.json").read_text())
    assert snapshot["recorded_reason"] == opaque
    markdown = (output / "record.md").read_text()
    fact = next(
        line.removeprefix("- Recorded reason: ")
        for line in markdown.splitlines()
        if line.startswith("- Recorded reason: ")
    )
    delimiter = fact[: len(fact) - len(fact.lstrip("`"))]
    assert delimiter, "opaque recorded data must be contained in a literal code span"
    assert fact.endswith(delimiter) and len(fact) > 2 * len(delimiter)
    literal = fact[len(delimiter) : -len(delimiter)]
    assert "invalid.example" in literal
    # A same-length backtick run would close the public literal representation.
    active = 0
    for character in literal + " ":
        if character == "`":
            active += 1
        elif active:
            assert active != len(delimiter)
            active = 0
    assert "[explanation.json](explanation.json)" in markdown
    assert "[report.html](report.html)" in markdown

"""Explicit fresh-file projection of existing records; never execute checks."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_harness import __version__, explanation, html_report
from agent_harness.repository import (
    _is_within_directory,
    resolve_repository_root,
    validate_runtime_state_directory,
)


def _absolute(path: Path | str) -> Path:
    selected = Path(path).expanduser()
    # Collapsing link/.. lexically can select a different protected location.
    # Refuse that ambiguity for every input/output boundary before normalization.
    if ".." in selected.parts:
        for component in (*reversed(selected.parents), selected):
            if component.is_symlink():
                raise ValueError("report path combines symlinks and parent traversal")
    return Path(os.path.abspath(selected))


def _validate_output(
    destination: Path, root: Path, protected: tuple[Path, ...], *, fresh: bool
) -> None:
    metadata = validate_runtime_state_directory(destination, root)
    for boundary in (root, *metadata, *protected):
        resolved = boundary.resolve()
        if _is_within_directory(
            destination.resolve(), resolved
        ) or _is_within_directory(resolved, destination.resolve()):
            raise ValueError("report output overlaps protected input")
    if not destination.parent.is_dir():
        raise ValueError("report output parent must exist")
    if fresh and destination.exists():
        raise ValueError("report output must be fresh")


def _text(value: object) -> str:
    """Opaque recorded values may not create Markdown links or HTML elements."""
    if isinstance(value, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", value):
        return value
    text = html.escape(json.dumps(value, ensure_ascii=False), quote=False)
    for token in ("\\", "`", "*", "_", "[", "]", "(", ")", "!", "#", "|", "~"):
        text = text.replace(token, "\\" + token)
    delimiter = "`" * (
        max((len(run) for run in re.findall(r"`+", text)), default=0) + 1
    )
    return delimiter + text + delimiter


def _renderer_digest() -> str:
    try:
        with Path(html_report.__file__).open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) <= 1024 * 1024:
            return hashlib.sha256(raw).hexdigest()
    except OSError:
        pass
    return "unavailable"


def _markdown(snapshot: dict[str, object]) -> str:
    details = snapshot["explanation"]
    assert isinstance(details, dict)
    lines = [
        "# Verification record",
        "",
        "Generated from one historical snapshot; no checks were executed.",
        "",
        "- Saved snapshot: [explanation.json](explanation.json)",
        "- Readable view: [report.html](report.html)",
        f"- Mercury package version: {_text(__version__)}",
        f"- Renderer file SHA-256: {_renderer_digest()}",
        "- Generator Git revision: unavailable; package version is not a source digest.",
        f"- Inspection time (not execution time): {_text(details.get('observed_at'))}",
        f"- Recorded execution status: {_text(snapshot.get('recorded_status'))}",
        f"- Recorded reason: {_text(snapshot.get('recorded_reason'))}",
        f"- History diagnostics: {_text(snapshot.get('diagnostics'))}",
        f"- Target revision: {_text(snapshot.get('target_revision'))}",
        f"- Evidence readiness: {_text(details.get('evidence_readiness'))}",
        f"- Conclusion: {_text(details.get('conclusion'))}",
        f"- Blockers: {_text(details.get('blockers'))}",
        f"- Limits: {_text(details.get('limits'))}",
        "- Current workspace verification: unavailable; no fresh capture.",
        "- Independent review / approval: not recorded by this exporter.",
        "- Delivery: unavailable; publication and merge are not recorded here.",
        "- Manual decision rationale: not recorded; no decision was inferred.",
        "",
        "## Saved native attempts",
        "",
    ]
    attempts = details.get("attempts", [])
    assert isinstance(attempts, list)
    if not attempts:
        lines.append("Saved native process/checker metadata: unavailable.")
    for index, attempt in enumerate(attempts, 1):
        assert isinstance(attempt, dict)
        lines.extend(
            [
                f"### Attempt {index}",
                "",
                f"- Identity: {_text(attempt.get('attempt_id'))}",
                f"- Repository: {_text(attempt.get('repository_root'))}",
                f"- Target revision: {_text(attempt.get('target_revision'))}",
                f"- Candidate binding: {_text(attempt.get('candidate_binding'))}",
                f"- Process metadata: {_text(attempt.get('native_process'))}",
                f"- Checker result: {_text(attempt.get('checker_result'))}",
                f"- Saved command outcomes: {_text(attempt.get('commands'))}",
                "",
            ]
        )
    lines.extend(["", "## External checks", ""])
    checks = details.get("external_checks", [])
    assert isinstance(checks, list)
    if not checks:
        lines.append("External checks: not supplied.")
    for index, check in enumerate(checks, 1):
        assert isinstance(check, dict)
        lines.extend(
            [
                f"### External check {index}",
                "",
                *[
                    f"- {label}: {_text(check.get(key))}"
                    for key, label in (
                        ("actor", "Recorded actor"),
                        ("check_label", "Label"),
                        ("criterion", "Criterion"),
                        ("state", "Receipt readability"),
                        ("outcome", "Verification outcome"),
                        ("process", "Process outcome"),
                        ("tests", "Assertion state and counts"),
                        ("historical_applicability", "Historical applicability"),
                        ("subject_state", "Subject observation"),
                        ("protected_state", "Protected observation"),
                        ("receipt", "Original receipt"),
                        ("artifact_path", "Checker-owned artifact"),
                        ("limits", "Limits"),
                    )
                ],
                "",
            ]
        )
    lines.extend(
        [
            "No cross-check or cross-attempt passing total is calculated.",
            "Hashes identify bytes, not authors or semantic correctness.",
            "Original evidence stays at its recorded location; this is not a portable export.",
            "",
        ]
    )
    return "\n".join(lines)


def export_report(
    state_directory: Path | str,
    *,
    repository: Path | str,
    output_directory: Path | str,
    external_checks: Sequence[Path | str] = (),
    protected_paths: Sequence[Path | str] = (),
    config_path: Path | str | None = None,
    expected_revision: str | None = None,
    expected_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Write three exclusive projections; retain partial files after failure.

    Boundary checks are point-in-time checks, not concurrent-path confinement.
    A generated UNKNOWN/FAIL report is not verification success or authority.
    """
    if isinstance(external_checks, (str, Path)) or len(external_checks) > 32:
        raise ValueError("report requires at most 32 explicit receipt paths")
    if isinstance(protected_paths, (str, Path)):
        raise ValueError("protected paths must be a sequence")
    selected_repository = _absolute(repository)
    if selected_repository.is_symlink():
        raise ValueError("report repository selection must not be a terminal symlink")
    root = resolve_repository_root(selected_repository)
    destination = _absolute(output_directory)
    protected = (
        _absolute(state_directory),
        *(_absolute(path).parent for path in external_checks),
        *(_absolute(path) for path in protected_paths),
        *((_absolute(config_path),) if config_path is not None else ()),
    )
    _validate_output(destination, root, protected, fresh=True)
    snapshot = explanation.explain_execution(
        state_directory,
        config_path=config_path,
        expected_revision=expected_revision,
        expected_candidate_sha256=expected_candidate_sha256,
        external_checks=external_checks,
    )
    contents = {
        "explanation.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        "report.html": html_report.render_execution_html(snapshot),
        "record.md": _markdown(snapshot),
    }
    _validate_output(destination, root, protected, fresh=True)
    destination.mkdir(mode=0o700)
    for name, content in contents.items():
        _validate_output(destination, root, protected, fresh=False)
        with (destination / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
    return {
        "generation": "complete",
        "message": "기존 기록에서 새 보고서 묶음을 생성했습니다. 검사를 실행하지 않았습니다.",
        "output_directory": str(destination),
        "history_diagnostics": snapshot["diagnostics"],
        "recorded_status": snapshot["recorded_status"],
        "verification_success": "not_inferred",
    }

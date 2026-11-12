"""Recorded offline cart scenarios use the real Mercury executor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def invoke(
    parent: Path, scenario: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    parent.mkdir(mode=0o700, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/quickstart.py"),
            "--approve-demo",
            "--fixture",
            "cart",
            "--scenario",
            scenario,
            "--evidence-parent",
            str(parent),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.stdout, result.stderr
    value = json.loads(result.stdout)
    return result, Path(value["evidence_directory"])


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("correct", "pass"),
        ("regression", "human"),
        ("scope_escape", "human"),
        ("incomplete", "unknown"),
    ],
)
def test_real_cart_outcomes(tmp_path: Path, scenario: str, status: str) -> None:
    result, run = invoke(tmp_path / "private", scenario)
    capture = json.loads((run / "capture.json").read_text())
    assert capture["recordedStatus"] == status
    assert result.returncode == (0 if status == "pass" else 1)
    assert (run / "state/events.jsonl").exists()
    assert capture["beforeFiles"] != capture["afterFiles"]
    rows = capture["checks"]
    assert [r["exitCode"] for r in rows if r["phase"] == "baseline"] == [1, 0]
    if scenario == "correct":
        assert [r["exitCode"] for r in rows if r["phase"] == "final"] == [0, 0]
    elif scenario == "regression":
        shipping = next(
            r
            for r in rows
            if r["phase"] == "development" and r["commandId"] == "shipping"
        )
        assert shipping["exitCode"] == 1
        assert any(
            c["expected"] == 40000 and c["actual"] == 43000 for c in shipping["cases"]
        )
    elif scenario == "scope_escape":
        assert not [r for r in rows if r["phase"] != "baseline"]
    else:
        assert capture["recordedReason"] == "interrupted"
        assert not [r for r in rows if r["phase"] == "final"]
        events = [
            json.loads(line)
            for line in (run / "state/events.jsonl").read_text().splitlines()
        ]
        assert any(
            e.get("event") == "verification_phase_intent" and e.get("phase") == "final"
            for e in events
        )


def test_no_approval_has_no_side_effect(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "examples/quickstart.py",
            "--fixture",
            "cart",
            "--evidence-parent",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_reject_source_and_nonprivate_parent_before_fixture(tmp_path: Path) -> None:
    for parent in (ROOT, tmp_path):
        if parent == tmp_path:
            parent.chmod(0o755)
        result = subprocess.run(
            [
                sys.executable,
                "examples/quickstart.py",
                "--approve-demo",
                "--fixture",
                "cart",
                "--evidence-parent",
                str(parent),
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_wrapper_preserves_bound_selections(tmp_path: Path) -> None:
    import importlib

    from agent_harness.policy import VerificationPolicy
    from agent_harness.ports.verification import (
        CommandResult,
        VerificationExecutables,
    )

    sys.path.insert(0, str(ROOT / "examples"))
    try:
        fixture = importlib.import_module("cart_fixture")
    finally:
        sys.path.pop(0)
    selections = VerificationExecutables(str(tmp_path), str(tmp_path), ())
    command = (sys.executable, "-B", "check_discount.py")
    policy = VerificationPolicy((command,), ".", 3)

    class Delegate:
        def bind(
            self, policy: VerificationPolicy, repository: str | Path
        ) -> VerificationExecutables:
            return selections

        def run(
            self,
            policy: VerificationPolicy,
            repository: str | Path,
            *,
            selections: VerificationExecutables | None = None,
        ) -> tuple[CommandResult, ...]:
            assert selections is selected
            return (CommandResult(command, 0, "[]", "", 0.0, False),)

    wrapper = fixture.CartExecutor(tmp_path / "state", "correct", Delegate())
    selected = wrapper.bind(policy, tmp_path)
    assert selected is selections
    wrapper.run(policy, tmp_path, selections=selected)

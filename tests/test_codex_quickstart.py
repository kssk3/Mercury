"""Contracts for a native integration example without real model invocation."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "codex_quickstart.py"


def invoke(
    tmp_path: Path, *args: str, mode: str = "fix"
) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "fake-codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"mode = {mode!r}\n"
        f"calls = pathlib.Path({str(tmp_path / 'calls')!r})\n"
        'with calls.open("a") as stream: stream.write(" ".join(sys.argv[1:]) + "\\n")\n'
        'if sys.argv[1:] == ["login", "status"]:\n'
        '    print("Logged in using ChatGPT", file=sys.stderr); sys.exit(0)\n'
        'assert "exec" in sys.argv and "--ephemeral" in sys.argv\n'
        'assert sys.argv[sys.argv.index("-a") + 1] == "never"\n'
        "prompt = json.load(sys.stdin)\n"
        'assert prompt["task_contract"]["allowed_paths"] == ["calculator.py"]\n'
        'if mode == "fail": sys.exit(7)\n'
        'if mode == "fix":\n'
        '    pathlib.Path("calculator.py").write_text('
        '"def discount(price, percent):\\n    return price * (1 - percent / 100)\\n")\n'
        'if mode == "protect": pathlib.Path("check.py").write_text("pass\\n")\n'
        'pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("done")\n'
        'print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 5}}))\n',
        encoding="utf-8",
    )
    fake.chmod(0o700)
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        'model = "fixture-model"\nmodel_reasoning_effort = "high"\n'
    )
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    env["CODEX_HOME"] = str(home)
    return subprocess.run(
        (
            sys.executable,
            str(EXAMPLE),
            "--codex-executable",
            str(fake),
            "--evidence-parent",
            str(tmp_path),
            *args,
        ),
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_unapproved_run_creates_no_fixture_or_native_call(tmp_path: Path) -> None:
    """C1: the explicit approval gate precedes fixture creation and native calls."""
    result = invoke(tmp_path)
    assert result.returncode == 2
    assert "approve-codex-demo" in result.stderr
    assert not (tmp_path / "calls").exists()
    assert not list(tmp_path.glob("mercury-codex-*"))


def summary(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_success_requires_checks_and_preserves_external_receipts(
    tmp_path: Path,
) -> None:
    """C2/C5/C6: real adapter with fake CLI yields attributable checks and cleanup."""
    result = invoke(tmp_path, "--approve-codex-demo")
    assert result.returncode == 0, result.stderr + result.stdout
    facts = summary(result)
    assert facts["status"] == "pass"
    assert facts["baseline"] == "FAIL"
    assert facts["development"] == facts["final"] == "PASS"
    assert facts["command_boundaries"] == 4
    assert facts["criteria_passed"] is True
    assert facts["scope_clear"] is True
    assert facts["protection_clear"] is True
    assert facts["changed_paths"] == ["calculator.py"]
    native = facts["native"]
    assert isinstance(native, dict)
    assert native["attempts"] == 1
    assert native["usage"] == {"input_tokens": 12, "output_tokens": 5}
    evidence = Path(str(facts["evidence_directory"]))
    assert (evidence / "private-native.json").exists()
    assert (evidence / "summary.json").exists()
    assert not (evidence / "state" / "lock").exists()
    events = [
        json.loads(line)
        for line in (evidence / "state" / "events.jsonl").read_text().splitlines()
    ]
    root = next(
        event["repository_root"] for event in events if event["event"] == "turn_intent"
    )
    assert not Path(root).exists()
    assert facts["fixture_removed"] is True
    assert (tmp_path / "codex-home" / "config.toml").read_text() == (
        'model = "fixture-model"\nmodel_reasoning_effort = "high"\n'
    )


def test_native_failure_never_retries_and_preserves_receipt(tmp_path: Path) -> None:
    """C4: a nonzero native turn cannot trigger another attempt."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="fail")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    native = facts["native"]
    assert isinstance(native, dict)
    assert native["attempts"] == 1
    assert native["exit_code"] == 7
    calls = (tmp_path / "calls").read_text().splitlines()
    assert sum("exec" in call.split() for call in calls) == 1
    evidence = Path(str(facts["evidence_directory"]))
    assert (evidence / "private-native.json").exists()
    assert not (evidence / "state" / "lock").exists()


def test_native_success_message_cannot_clear_protected_changes(tmp_path: Path) -> None:
    """C3: exit zero and model text do not permit protected edits."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="protect")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    assert facts["scope_clear"] is False
    assert facts["criteria_passed"] is False
    changed_paths = facts["changed_paths"]
    assert isinstance(changed_paths, list)
    assert "check.py" in changed_paths


def test_native_success_without_fix_cannot_substitute_for_check(tmp_path: Path) -> None:
    """C2/C5: a successful CLI process cannot establish fixture correctness."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="noop")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    assert facts["criteria_passed"] is False
    assert facts["baseline"] == "FAIL"
    assert facts["final"] != "PASS"


def test_api_key_override_is_refused_before_work(tmp_path: Path) -> None:
    """C7: refuse account/billing overrides without any native launch."""
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = "synthetic-test-override"
    result = subprocess.run(
        (
            sys.executable,
            str(EXAMPLE),
            "--approve-codex-demo",
            "--codex-executable",
            "/never-call-real-codex",
        ),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 2
    assert "API-key overrides" in result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))


@pytest.fixture
def example_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_quickstart", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cleanup_failure_preserves_receipt_without_removal_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, example_module: ModuleType
) -> None:
    """C5/C6: failed context cleanup cannot establish fixture removal."""
    scratch = tmp_path / "owned-fixture"
    scratch.mkdir()

    @contextmanager
    def failed_cleanup(*, prefix: str) -> Iterator[str]:
        yield str(scratch)
        raise OSError("owned fixture cleanup failed")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setattr(example_module.shutil, "which", lambda value: sys.executable)
    monkeypatch.setattr(example_module, "configuration", lambda: {})
    monkeypatch.setattr(
        example_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Logged in using ChatGPT", stderr=""
        ),
    )
    monkeypatch.setattr(example_module, "git", lambda *args: None)
    monkeypatch.setattr(example_module, "build_context_packet", lambda *a, **k: None)
    monkeypatch.setattr(
        example_module,
        "run_loop",
        lambda *args, **kwargs: SimpleNamespace(
            status="pass",
            reason="completed",
            usage=SimpleNamespace(commands=0),
            criteria=(),
            report=None,
        ),
    )
    monkeypatch.setattr(example_module.tempfile, "TemporaryDirectory", failed_cleanup)
    result = example_module.main(
        ["--approve-codex-demo", "--evidence-parent", str(tmp_path)]
    )
    assert result == 1
    receipts = list(tmp_path.glob("mercury-codex-*/summary.json"))
    assert len(receipts) == 1
    facts = json.loads(receipts[0].read_text())
    assert facts["status"] == "unknown"
    assert facts["reason"] == "OSError"
    assert facts.get("fixture_removed") is not True
    assert scratch.exists()
    assert not (receipts[0].parent / "state" / "lock").exists()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "turn.completed"}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": None}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": {}}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": {"other": 3}}, "UNKNOWN"),
        (
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": -1,
                    "cached_input_tokens": True,
                    "output_tokens": "5",
                },
            },
            "UNKNOWN",
        ),
        (
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            },
            {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 5},
        ),
        (
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": None},
            },
            {"input_tokens": 12},
        ),
    ],
)
def test_usage_reports_unknown_only_without_valid_fields(
    tmp_path: Path,
    example_module: ModuleType,
    event: dict[str, object],
    expected: object,
) -> None:
    """C5: absent/invalid usage is unknown; nonnegative integers include zero."""
    adapter = example_module.RecordingAdapter("never-invoked", tmp_path)
    captured = SimpleNamespace(
        text=json.dumps(event), retained_bytes=0, truncated=False
    )
    adapter.result = SimpleNamespace(
        stdout=captured,
        stderr=captured,
        final_message=None,
        process_status="completed",
        exit_code=0,
        duration_seconds=0,
        final_output_status="missing",
    )
    assert adapter.facts()["usage"] == expected

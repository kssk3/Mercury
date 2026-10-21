from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "javascript_quickstart.py"
NODE = next(
    (
        path.resolve()
        for path in (
            Path("/opt/homebrew/bin/node"),
            Path("/usr/bin/node"),
            Path("/usr/local/bin/node"),
        )
        if path.is_file()
    ),
    Path("/nonexistent/trusted-node"),
)


def module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mercury_javascript_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fake_claude(tmp_path: Path, mode: str) -> Path:
    native = tmp_path / "fake-claude"
    native.write_text(
        f"#!{sys.executable}\n"
        "import json,pathlib,sys\n"
        "data=json.loads(sys.stdin.read())\n"
        f"mode={mode!r}\n"
        "if mode=='fail': raise SystemExit(7)\n"
        "pathlib.Path('add.mjs').write_text('export function add(a,b) { return a+b; }\\n')\n"
        "if mode=='protected': pathlib.Path('check.mjs').write_text('process.exit(0);\\n')\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'synthetic-secret fixed'}))\n"
    )
    native.chmod(0o700)
    return native


def test_missing_approval_never_creates_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence"
    assert (
        module().main(
            [
                "--node",
                str(NODE),
                "--executable",
                sys.executable,
                "--evidence-dir",
                str(evidence),
            ]
        )
        == 2
    )
    assert not evidence.exists()
    assert "approval_required" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["fix", "fail", "protected"])
def test_actual_cli_to_loop_fake_claude_retains_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    if not NODE.is_file():
        pytest.skip("trusted local Node unavailable; no Node evidence claimed")
    evidence = tmp_path / "evidence"
    example = module()
    code = example.main(
        [
            "--approve-demo",
            "--node",
            str(NODE),
            "--executable",
            str(fake_claude(tmp_path, mode)),
            "--evidence-dir",
            str(evidence),
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert code == (0 if mode == "fix" else 1)
    assert (
        summary["result"]["status"] == "pass"
        if mode == "fix"
        else summary["result"]["status"] != "pass"
    )
    assert summary["evidence_dir"] == str(evidence)
    assert "synthetic-secret" not in captured.out
    assert (evidence / "run.json").exists()
    assert list((evidence / "state").rglob("events.jsonl"))
    if mode != "protected":
        assert (evidence / "repository" / "check.mjs").read_text() == example.CHECK
    if mode == "fix":
        assert "a+b" in (evidence / "repository" / "add.mjs").read_text()


@pytest.mark.parametrize("kind", ["existing", "symlink", "repository", "relative"])
def test_unsafe_evidence_rejected_without_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    evidence = tmp_path / "evidence"
    if kind == "existing":
        evidence.mkdir()
        (evidence / "keep").write_text("keep")
    elif kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        evidence.symlink_to(target, target_is_directory=True)
    elif kind == "repository":
        evidence = EXAMPLE.parent.parent / "forbidden-example-evidence"
    elif kind == "relative":
        evidence = Path("relative-evidence")
    assert (
        module().main(
            [
                "--approve-demo",
                "--node",
                str(NODE),
                "--executable",
                sys.executable,
                "--evidence-dir",
                str(evidence),
            ]
        )
        == 2
    )
    assert "invalid_demo_inputs" in capsys.readouterr().err
    if kind == "existing":
        assert (evidence / "keep").read_text() == "keep"
    elif kind == "symlink":
        assert list(target.iterdir()) == []
    else:
        assert not evidence.exists()

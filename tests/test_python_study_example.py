"""Bounded integration contracts; these tests never invoke a real model."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/python_study_example.py"


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(EXAMPLE.parent))
    spec = importlib.util.spec_from_file_location("python_study_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_requires_approval_before_writes(tmp_path: Path, example: ModuleType) -> None:
    """No approval means exit2 before any copy, evidence or native operation."""
    before = list(tmp_path.iterdir())
    with pytest.raises(SystemExit) as stopped:
        example.main([])
    assert stopped.value.code == 2
    assert list(tmp_path.iterdir()) == before


def create_source(tmp_path: Path, example: ModuleType) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "chapter09").mkdir()
    (source / "chapter09/03.py").write_text(
        "import sys\nargs = sys.argv[1:]\nprint(int(args[0]) + int(args[1]))\n"
    )
    for name in example.PROTECTED[1:]:
        (source / name).write_text("# synthetic protected setting\n")
    example.git(source, "init", "--template=", "--initial-branch=fixture")
    example.git(source, "add", ".")
    example.git(source, "commit", "-m", "Synthetic source for native-free tests")
    example.__dict__["SOURCE_REVISION"] = example.git(source, "rev-parse", "HEAD")
    return source


def test_copy_uses_committed_source_and_keeps_dirty_work(
    tmp_path: Path, example: ModuleType
) -> None:
    source = create_source(tmp_path, example)
    original = (source / "chapter09/03.py").read_bytes()
    (source / "chapter09/03.py").write_text("dirty user work\n")
    (source / "untracked.txt").write_text("private user work\n")
    before = example.git(source, "status", "--porcelain")
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    manifest = example.prepare(source, root, evidence, Path(sys.executable))
    assert (root / "chapter09/03.py").read_bytes() == original
    assert not (root / "untracked.txt").exists()
    assert example.git(root, "rev-parse", "HEAD^1^{tree}") == manifest["source_tree"]
    assert manifest["prepared_head"] != manifest["source_head"]
    assert example.git(root, "remote") == ""
    assert example.git(root, "status", "--porcelain") == ""
    assert example.git(source, "status", "--porcelain") == before
    assert (source / "chapter09/03.py").read_text() == "dirty user work\n"
    assert not (root / ".git/objects/info/alternates").exists()


def test_wrong_revision_and_copied_native_config_refused_before_copy(
    tmp_path: Path, example: ModuleType
) -> None:
    source = create_source(tmp_path, example)
    example.__dict__["SOURCE_REVISION"] = "0" * 40
    with pytest.raises(ValueError, match="revision"):
        example.prepare(
            source, tmp_path / "copy", tmp_path / "evidence", Path(sys.executable)
        )
    assert not (tmp_path / "copy").exists()
    (source / ".codex").mkdir()
    (source / ".codex/config.toml").write_text('model="unsupported"\n')
    example.git(source, "add", ".codex")
    example.git(source, "commit", "-m", "Synthetic native configuration")
    example.__dict__["SOURCE_REVISION"] = example.git(source, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="native settings"):
        example.prepare(
            source, tmp_path / "copy", tmp_path / "evidence", Path(sys.executable)
        )
    assert not (tmp_path / "copy").exists()


@pytest.mark.parametrize("compliant", [False, True])
def test_checker_distinguishes_contract_failure_from_success(
    tmp_path: Path, example: ModuleType, compliant: bool
) -> None:
    import json
    import os
    import subprocess

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    if compliant:
        (root / "chapter09/03.py").write_text(COMPLIANT)
    result = subprocess.run(
        [sys.executable, "-B", str(root / example.CHECK_PATH)],
        cwd=root,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (0 if compliant else 1), result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "normal_passed": 3,
        "invalid_passed": 5 if compliant else 0,
        "cases": 8,
    }


COMPLIANT = """import sys
if len(sys.argv) != 3:
    print("Provide two integers", file=sys.stderr)
    raise SystemExit(2)
try:
    first, second = map(int, sys.argv[1:])
except ValueError:
    print("Provide two integers", file=sys.stderr)
    raise SystemExit(2)
print(first + second)
"""


@pytest.mark.parametrize("mode", ["fix", "noop", "protect"])
def test_fake_native_has_one_attempt_and_attributable_phases(
    tmp_path: Path, example: ModuleType, mode: str
) -> None:
    import json

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    original_source = (source / "chapter09/03.py").read_bytes()
    fake = tmp_path / "fake-codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json,pathlib,sys\n"
        "prompt=json.load(sys.stdin)\n"
        "assert prompt['task_contract']['allowed_paths']==['chapter09/03.py']\n"
        f"mode={mode!r}\n"
        f"code={COMPLIANT!r}\n"
        "if mode=='fix': pathlib.Path('chapter09/03.py').write_text(code)\n"
        "if mode=='protect': pathlib.Path('p2_checks/check_cli_sum.py').write_text('pass\\n')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n"
    )
    fake.chmod(0o700)
    facts = example.execute(
        root,
        evidence,
        Path(sys.executable),
        str(fake),
        {"model": "synthetic"},
        source=source,
    )
    assert facts["native"]["attempts"] == 1
    assert (source / "chapter09/03.py").read_bytes() == original_source
    assert root.exists() and (evidence / "private-native.json").exists()
    assert not (evidence / "state/lock").exists()
    if mode == "fix":
        assert facts["baseline"] == "FAIL"
        assert facts["status"] == "pass", facts
        assert facts["development"] == facts["final"] == "PASS"
        assert (
            facts["criteria_passed"]
            is facts["scope_clear"]
            is facts["protection_clear"]
            is True
        )
        assert facts["command_boundaries"] == 4
        assert facts["changed_paths"] == ["chapter09/03.py"]
        assert facts["history"]["recorded_status"] == "pass"
    elif mode == "noop":
        assert facts["baseline"] == "FAIL"
        assert facts["status"] != "pass"
        assert facts["final"] == "UNKNOWN"
    else:
        assert facts["status"] == "human"
        assert facts["reason"] == "unsafe_failure"
        assert facts["scope_clear"] is False
        assert facts["protection_clear"] is False
        assert facts["criteria_passed"] is False
        assert facts["baseline"] == facts["development"] == facts["final"] == "UNKNOWN"
        assert facts["command_boundaries"] == 2
        assert facts["changed_paths"] == [example.CHECK_PATH]
        assert facts["history"]["recorded_status"] == "human"
        assert facts["history"]["recorded_reason"] == "unsafe_failure"
        assert facts["history"]["verification"] is None
        events = [
            json.loads(line)
            for line in (evidence / "state/events.jsonl").read_text().splitlines()
        ]
        assert any(
            event["event"] == "loop_protected_inputs"
            and any(check["state"] == "modified" for check in event["checks"])
            for event in events
        )
        assert any(
            event["event"] == "scope_decision"
            and event["proceed"] is False
            and event["protected_paths"] == [example.CHECK_PATH]
            for event in events
        )
        assert not any(
            event["event"] in ("verification_checkpoint", "attempt_verification")
            for event in events
        )
    assert json.loads((evidence / "summary.json").read_text()) == facts
    with pytest.raises(ValueError):
        example.execute(
            root, evidence, Path(sys.executable), str(fake), {}, source=source
        )


@pytest.mark.parametrize("destination", ["copy", "evidence", "alias"])
def test_pr7_outputs_never_write_inside_source(
    tmp_path: Path, example: ModuleType, destination: str
) -> None:
    """A1: both resolved destinations must stay outside source before writes."""
    source = create_source(tmp_path, example)
    before = example.git(source, "status", "--porcelain")
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    if destination == "copy":
        root = source / "owned-copy"
    elif destination == "evidence":
        evidence = source / "receipts"
    else:
        (tmp_path / "alias").symlink_to(source, target_is_directory=True)
        root = tmp_path / "alias" / "owned-copy"
    with pytest.raises(ValueError, match="source"):
        example.prepare(source, root, evidence, Path(sys.executable))
    assert example.git(source, "status", "--porcelain") == before
    assert not root.exists() and not evidence.exists()


def test_pr7_copy_contains_only_admitted_tree(
    tmp_path: Path, example: ModuleType
) -> None:
    """A2: unrelated refs and their private blob objects must not enter the copy."""
    source = create_source(tmp_path, example)
    admitted = example.SOURCE_REVISION
    (source / "private.txt").write_text("unadmitted private content")
    example.git(source, "add", "private.txt")
    example.git(source, "commit", "-m", "Unrelated private work")
    secret = example.git(source, "rev-parse", "HEAD:private.txt")
    example.git(source, "tag", "private-tag")
    example.git(source, "checkout", "--detach", admitted)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    manifest = example.prepare(source, root, evidence, Path(sys.executable))
    assert example.git(root, "tag") == ""
    import subprocess

    with pytest.raises(subprocess.CalledProcessError):
        example.git(root, "cat-file", "-e", secret)
    assert manifest["source_tree"] == example.git(
        source, "rev-parse", admitted + "^{tree}"
    )
    assert not (root / "private.txt").exists()


def test_pr7_checker_rejects_second_argument_conversion_escape(
    tmp_path: Path, example: ModuleType
) -> None:
    """A3: conversion guidance and exit2 apply to either argument position."""
    import subprocess

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    (root / example.TARGET).write_text(
        COMPLIANT.replace(
            "first, second = map(int, sys.argv[1:])", "first = int(sys.argv[1])"
        ).replace(
            "print(first + second)", "second = int(sys.argv[2])\nprint(first + second)"
        )
    )
    result = subprocess.run(
        [sys.executable, "-B", str(root / example.CHECK_PATH)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_pr7_stale_matching_checker_manifest_refused(
    tmp_path: Path, example: ModuleType
) -> None:
    """A4: a self-consistent old checker cannot satisfy current criteria."""
    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    example.__dict__["CHECKER"] += "\n# strengthened current checker\n"
    with pytest.raises(ValueError, match="baseline"):
        example.execute(
            root,
            evidence,
            Path(sys.executable),
            str(tmp_path / "absent"),
            {},
            source=source,
        )
    assert not (evidence / "state").exists()


@pytest.mark.parametrize("mode", ["metadata", "hidden", "ignored", "delete", "symlink"])
def test_pr7_independent_guard_never_persists_false_success(
    tmp_path: Path, example: ModuleType, mode: str
) -> None:
    """A5: Git-independent scope and metadata checks invalidate native success."""
    import json

    source = create_source(tmp_path, example)
    if mode == "ignored":
        (source / ".gitignore").write_text("hidden.txt\n")
        example.git(source, "add", ".gitignore")
        example.git(source, "commit", "-m", "Admitted ignore pattern")
        example.__dict__["SOURCE_REVISION"] = example.git(source, "rev-parse", "HEAD")
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    actions = {
        "metadata": "pathlib.Path('.git/info').mkdir(); pathlib.Path('.git/info/exclude').write_text('# changed metadata\\n')",
        "hidden": "pathlib.Path('hidden.txt').write_text('hidden'); pathlib.Path('.git/info').mkdir(); pathlib.Path('.git/info/exclude').write_text('hidden.txt\\n')",
        "ignored": "pathlib.Path('hidden.txt').write_text('hidden')",
        "delete": "pathlib.Path('uv.lock').unlink()",
        "symlink": "pathlib.Path('hidden.txt').symlink_to('/dev/null')",
    }
    fake = tmp_path / "fake-codex"
    fake.write_text(
        f"#!{sys.executable}\nimport json,pathlib,sys\n"
        "json.load(sys.stdin)\n"
        f"pathlib.Path('chapter09/03.py').write_text({COMPLIANT!r})\n"
        + actions[mode]
        + "\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    fake.chmod(0o700)
    facts = example.execute(
        root, evidence, Path(sys.executable), str(fake), {}, source=source
    )
    assert facts["native"]["attempts"] == 1
    assert facts["status"] != "pass", facts
    assert facts["scope_clear"] is False
    assert facts["protection_clear"] is False
    assert facts["history"]["recorded_status"] != "pass"
    assert json.loads((evidence / "summary.json").read_text()) == facts


@pytest.mark.parametrize("mode", ["native_config", "metadata", "ignored"])
def test_pr7_between_phase_tampering_refused(
    tmp_path: Path, example: ModuleType, mode: str
) -> None:
    """B1: retained-copy mutations must be refused before launch/state creation."""
    source = create_source(tmp_path, example)
    if mode == "ignored":
        (source / ".gitignore").write_text("hidden.txt\n")
        example.git(source, "add", ".gitignore")
        example.git(source, "commit", "-m", "Admitted ignore pattern")
        example.__dict__["SOURCE_REVISION"] = example.git(source, "rev-parse", "HEAD")
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    if mode == "native_config":
        (root / ".codex").mkdir()
        (root / ".codex/config.toml").write_text('model="synthetic-unadmitted"\n')
        (root / ".git/info").mkdir()
        (root / ".git/info/exclude").write_text(".codex/\n")
    elif mode == "metadata":
        with (root / ".git/config").open("a") as config:
            config.write("\n# changed between preparation and execution\n")
    else:
        (root / "hidden.txt").write_text("unadmitted ignored content")
    fake, marker = launch_probe(tmp_path)
    with pytest.raises(ValueError, match="baseline"):
        example.execute(
            root, evidence, Path(sys.executable), str(fake), {}, source=source
        )
    assert not marker.exists()
    assert not (evidence / "state").exists()
    assert not (evidence / "summary.json").exists()


def launch_probe(tmp_path: Path) -> tuple[Path, Path]:
    """A valid native-shaped synthetic executable makes launch observable."""
    fake, marker = tmp_path / "fake-codex", tmp_path / "launched"
    fake.write_text(
        f"#!{sys.executable}\nimport json,pathlib,sys\njson.load(sys.stdin)\n"
        f"pathlib.Path({str(marker)!r}).write_text('launched')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    fake.chmod(0o700)
    return fake, marker


def test_pr7_missing_preparation_snapshot_refused(
    tmp_path: Path, example: ModuleType
) -> None:
    """B1: legacy provenance cannot silently adopt a new execution baseline."""
    import json

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    provenance = evidence / "preparation.json"
    data = json.loads(provenance.read_text())
    data.pop("prepared_snapshot", None)
    provenance.write_text(json.dumps(data))
    fake, marker = launch_probe(tmp_path)
    with pytest.raises(ValueError, match="baseline"):
        example.execute(
            root, evidence, Path(sys.executable), str(fake), {}, source=source
        )
    assert not marker.exists()
    assert not (evidence / "state").exists()


@pytest.mark.parametrize("replacement", ["commit", "blob"])
def test_pr7_replacement_refs_never_change_admitted_source(
    tmp_path: Path, example: ModuleType, replacement: str
) -> None:
    """C2: local replacements must not change pinned objects or user work."""
    import json

    source = create_source(tmp_path, example)
    admitted = example.SOURCE_REVISION
    admitted_tree = example.git(source, "rev-parse", admitted + "^{tree}")
    original = (source / example.TARGET).read_bytes()
    original_blob = example.git(source, "rev-parse", admitted + ":" + example.TARGET)
    (source / example.TARGET).write_text("print('unadmitted replacement')\n")
    (source / "unadmitted.txt").write_text("private replacement content\n")
    example.git(source, "add", ".")
    example.git(source, "commit", "-m", "Unadmitted replacement objects")
    replacement_commit = example.git(source, "rev-parse", "HEAD")
    replacement_blob = example.git(source, "rev-parse", "HEAD:" + example.TARGET)
    example.git(source, "checkout", "--detach", admitted)
    example.git(
        source,
        "replace",
        admitted if replacement == "commit" else original_blob,
        replacement_commit if replacement == "commit" else replacement_blob,
    )
    (source / example.TARGET).write_text("dirty user work\n")
    (source / "user-untracked.txt").write_text("private untracked work\n")
    before_refs = example.git(source, "show-ref")
    before_status = example.git(source, "status", "--porcelain")
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    error = None
    manifest: dict[str, object] = {}
    try:
        manifest = example.prepare(source, root, evidence, Path(sys.executable))
    except ValueError as stopped:
        error = str(stopped)
    (tmp_path / "replacement-observation.json").write_text(
        json.dumps(
            {
                "replacement": replacement,
                "prepare_error": error,
                "copied_target": (root / example.TARGET).read_text(),
                "unadmitted_copied": (root / "unadmitted.txt").exists(),
                "manifest": manifest,
            },
            indent=2,
        )
    )
    assert example.git(source, "show-ref") == before_refs
    assert example.git(source, "status", "--porcelain") == before_status
    assert (source / example.TARGET).read_text() == "dirty user work\n"
    assert (source / "user-untracked.txt").read_text() == "private untracked work\n"
    assert error is None, "replacement refs changed the admitted tree: " + str(error)
    assert (root / example.TARGET).read_bytes() == original
    assert not (root / "unadmitted.txt").exists()
    assert manifest["source_tree"] == admitted_tree


def test_pr7_checker_startup_cannot_fake_success(
    tmp_path: Path, example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3: caller startup code cannot forge checker results for a native no-op."""
    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    fake, launched = launch_probe(tmp_path)
    poison, marker = tmp_path / "python-startup", tmp_path / "checker-startup"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text(
        "import json,os,pathlib,sys\n"
        "if sys.argv[0].endswith('check_cli_sum.py'):\n"
        f"    marker=pathlib.Path({str(marker)!r})\n"
        "    count=int(marker.read_text()) if marker.exists() else 0\n"
        "    marker.write_text(str(count+1))\n"
        "    print(json.dumps({'normal_passed':3,'invalid_passed':0 if count==0 else 5,'cases':8}),flush=True)\n"
        "    os._exit(1 if count==0 else 0)\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(poison))
    facts = example.execute(
        root, evidence, Path(sys.executable), str(fake), {}, source=source
    )
    assert launched.exists()
    assert facts["status"] != "pass", facts
    assert not marker.exists(), "caller startup ran before protected checker"


@pytest.mark.parametrize("branch", ["normal", "invalid"])
def test_pr7_target_startup_environment_is_isolated(
    tmp_path: Path, example: ModuleType, branch: str
) -> None:
    """C3: both target paths ignore startup code inherited by the checker."""
    import json
    import os
    import subprocess

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    (root / example.TARGET).write_text(COMPLIANT)
    poison, marker = tmp_path / "python-startup", tmp_path / "target-startup"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text(
        "import os,pathlib,sys\n"
        "if sys.argv[0].endswith('chapter09/03.py'):\n"
        f"    attack=(len(sys.argv)==3) if {branch!r}=='normal' else (len(sys.argv)!=3)\n"
        "    if attack:\n"
        f"        pathlib.Path({str(marker)!r}).write_text('startup executed')\n"
        "        print('poisoned',flush=True)\n"
        "        os._exit(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(root / example.CHECK_PATH)],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(poison), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    (tmp_path / "target-observation.json").write_text(
        json.dumps(
            {
                "branch": branch,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "startup_marker": marker.exists(),
            },
            indent=2,
        )
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "normal_passed": 3,
        "invalid_passed": 5,
        "cases": 8,
    }
    assert not marker.exists(), "caller startup ran before the target"


def test_pr7_trusted_git_selection_is_used(
    tmp_path: Path, example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: the selected external trusted Git performs the actual query."""
    import json

    from agent_harness import repository

    wrapper, marker = tmp_path / "trusted-git", tmp_path / "git-invoked.json"
    wrapper.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "os.execv('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setattr(repository, "git_executable", lambda: str(wrapper))
    version = example.git(tmp_path, "--version")
    arguments: list[str] = json.loads(marker.read_text()) if marker.exists() else []
    observation = {
        "version": version,
        "selected_executable": str(wrapper),
        "executed": marker.exists(),
        "arguments": arguments,
    }
    (tmp_path / "git-selection-observation.json").write_text(
        json.dumps(observation, indent=2)
    )
    assert marker.exists(), "trusted selector's external executable was bypassed"
    assert version.startswith("git version ")
    assert "--no-replace-objects" in arguments
    assert "core.hooksPath=/dev/null" in arguments


def test_pr7_relocated_evidence_rejected_before_configuration(
    tmp_path: Path, example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: CLI refuses relocated evidence before configuration or any receipt."""
    import json
    import os
    import shutil

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    relocated = source / "relocated-evidence"
    shutil.move(str(evidence), relocated)
    before = {path.name: path.read_bytes() for path in relocated.iterdir()}
    fake, marker = launch_probe(tmp_path)
    configuration_calls: list[str] = []

    def synthetic_configuration(executable: str) -> dict[str, object]:
        configuration_calls.append(executable)
        return {"model": "synthetic"}

    monkeypatch.setattr(example, "configuration", synthetic_configuration)
    for key in os.environ:
        if key.startswith(("OPENAI_", "CODEX_", "CHATGPT_")):
            monkeypatch.delenv(key)
    error = None
    try:
        example.main(
            [
                "--approve-python-study",
                "--source",
                str(source),
                "--working-copy",
                str(root),
                "--evidence-directory",
                str(relocated),
                "--check-python",
                sys.executable,
                "--codex-executable",
                str(fake),
            ]
        )
    except (ValueError, SystemExit) as stopped:
        error = str(stopped)
    observation = {
        "error": error,
        "configuration_reached": bool(configuration_calls),
        "native_reached": marker.exists(),
        "state_written": (relocated / "state").exists(),
        "summary_written": (relocated / "summary.json").exists(),
        "receipt_names": sorted(path.name for path in relocated.iterdir()),
    }
    (tmp_path / "relocated-evidence-observation.json").write_text(
        json.dumps(observation, indent=2)
    )
    assert not configuration_calls, (
        "configuration reached before source-boundary refusal"
    )
    assert error is not None, "relocated evidence was accepted"
    assert not marker.exists()
    assert {path.name: path.read_bytes() for path in relocated.iterdir()} == before


@pytest.mark.parametrize("destination", ["copy", "evidence"])
def test_pr7_execute_checks_bound_source_boundary(
    tmp_path: Path,
    example: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
) -> None:
    """R2: direct API resolves aliases and refuses source outputs before scanning."""
    import shutil

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    manifest = example.prepare(source, root, evidence, Path(sys.executable))
    assert manifest["source"] == str(source.resolve())
    relocated = source / "relocated"
    shutil.move(str(root if destination == "copy" else evidence), relocated)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    if destination == "copy":
        root = alias / "relocated"
    else:
        evidence = alias / "relocated"
    before = example.independent_snapshot(source)
    fake, marker = launch_probe(tmp_path)

    def unexpected_scan(path: Path) -> dict[str, tuple[int, str]]:
        raise AssertionError("metadata scan preceded source-boundary refusal")

    with monkeypatch.context() as patch:
        patch.setattr(example, "independent_snapshot", unexpected_scan)
        with pytest.raises(ValueError, match="source"):
            example.execute(
                root, evidence, Path(sys.executable), str(fake), {}, source=source
            )
    assert example.independent_snapshot(source) == before
    assert not marker.exists()
    assert not (evidence / "state").exists()
    assert not (evidence / "summary.json").exists()


def test_pr7_execute_rejects_wrong_bound_source(
    tmp_path: Path, example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: explicit source must equal provenance; absent legacy binding fails closed."""
    import json

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    other_source = tmp_path / "other-source"
    other_source.mkdir()
    fake, marker = launch_probe(tmp_path)

    def unexpected_scan(path: Path) -> dict[str, tuple[int, str]]:
        raise AssertionError("metadata scan preceded source-binding refusal")

    monkeypatch.setattr(example, "independent_snapshot", unexpected_scan)
    with pytest.raises(ValueError, match="source binding"):
        example.execute(
            root, evidence, Path(sys.executable), str(fake), {}, source=other_source
        )
    provenance = evidence / "preparation.json"
    manifest = json.loads(provenance.read_text())
    manifest.pop("source")
    provenance.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source binding"):
        example.execute(
            root, evidence, Path(sys.executable), str(fake), {}, source=source
        )
    assert not marker.exists()
    assert sorted(path.name for path in evidence.iterdir()) == ["preparation.json"]


def test_pr7_main_forwards_source_on_safe_execution(
    tmp_path: Path, example: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: a safe CLI execution passes canonical explicit source to its API."""
    import os

    source = create_source(tmp_path, example)
    root, evidence = tmp_path / "copy", tmp_path / "evidence"
    example.prepare(source, root, evidence, Path(sys.executable))
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    fake, marker = launch_probe(tmp_path)
    observed: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(
        example, "configuration", lambda executable: {"model": "synthetic"}
    )
    for key in os.environ:
        if key.startswith(("OPENAI_", "CODEX_", "CHATGPT_")):
            monkeypatch.delenv(key)

    def synthetic_execute(
        working_copy: Path,
        receipts: Path,
        check_python: Path,
        executable: str,
        selected: dict[str, object],
        *,
        source: Path,
    ) -> dict[str, object]:
        observed.append((source, working_copy, receipts))
        return {
            "status": "pass",
            "criteria_passed": True,
            "scope_clear": True,
            "protection_clear": True,
            "baseline_expected_behavioral_failure": True,
        }

    monkeypatch.setattr(example, "execute", synthetic_execute)
    assert (
        example.main(
            [
                "--approve-python-study",
                "--source",
                str(alias),
                "--working-copy",
                str(root),
                "--evidence-directory",
                str(evidence),
                "--check-python",
                sys.executable,
                "--codex-executable",
                str(fake),
            ]
        )
        == 0
    )
    assert observed == [(source.resolve(), root.resolve(), evidence.resolve())]
    assert not marker.exists()
    assert sorted(path.name for path in evidence.iterdir()) == ["preparation.json"]

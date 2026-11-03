"""Explicit isolated dispatch verifies identity before importing package code."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verified_runtime.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = (tmp_path / "runtime").resolve()
    package = root / "src/agent_harness"
    package.mkdir(parents=True)
    marker = tmp_path / "imported"
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(__file__)\n"
        "def main():\n    return 0\n"
    )
    manifest = tmp_path / "manifest.json"
    python = Path(sys.executable).resolve()
    data = {
        "schema_version": 1,
        "source_revision": "a" * 40,
        "runtime_root": str(root),
        "python": {"path": str(python), "sha256": digest(python)},
        "bootstrap": {"path": str(SCRIPT), "sha256": digest(SCRIPT)},
        "files": {
            str(p.relative_to(root / "src")): digest(p)
            for p in sorted((root / "src").rglob("*"))
            if p.is_file()
        },
    }
    manifest.write_text(json.dumps(data, sort_keys=True))
    return root, manifest, marker


def dispatch(
    root: Path,
    manifest: Path,
    *,
    anchor: str | None = None,
    arguments: tuple[str, ...] = ("sentinel",),
    timeout: int = 10,
    env: dict[str, str] | None = None,
    flags: tuple[str, ...] = ("-I", "-S", "-B"),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            *flags,
            str(SCRIPT),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
            "--manifest-sha256",
            anchor or digest(manifest),
            "--",
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def test_verified_launcher_dispatches_exact_source(tmp_path: Path) -> None:
    root, manifest, marker = bundle(tmp_path)
    result = dispatch(root, manifest)
    assert result.returncode == 0, result.stderr
    assert marker.exists(), "verified launcher did not dispatch the admitted CLI"
    assert marker.read_text() == str(root / "src/agent_harness/cli.py")


@pytest.mark.parametrize(
    "change",
    [
        "modified",
        "missing",
        "extra_python",
        "bytecode",
        "extension",
        "symlink_file",
        "symlink_directory",
        "hardlink",
        "fifo",
        "root_alias",
        "root_loop",
        "source_alias",
        "manifest_alias",
        "wrong_anchor",
        "bad_json",
        "duplicate_key",
        "absolute",
        "traversal",
        "dot_path",
        "backslash",
        "bad_digest",
        "wrong_root",
        "version",
        "unknown_field",
        "wrong_python",
        "wrong_bootstrap",
        "empty_inventory",
        "too_many_files",
        "oversized_manifest",
        "admitted_bytecode",
    ],
)
def test_integrity_failure_never_imports_cli(tmp_path: Path, change: str) -> None:
    root, manifest, marker = bundle(tmp_path)
    data = json.loads(manifest.read_text())
    cli = root / "src/agent_harness/cli.py"
    anchor = None
    raw = None
    if change == "modified":
        cli.write_text(cli.read_text() + "# altered\n")
    elif change == "missing":
        cli.unlink()
    elif change in {"extra_python", "bytecode", "extension", "admitted_bytecode"}:
        name = {
            "extra_python": "extra.py",
            "bytecode": "cli.pyc",
            "extension": "shadow.so",
            "admitted_bytecode": "cli.pyc",
        }[change]
        extra = cli.parent / name
        extra.write_bytes(b"unchecked code")
        if change == "admitted_bytecode":
            data["files"][str(extra.relative_to(root / "src"))] = digest(extra)
    elif change in {"symlink_file", "hardlink"}:
        external = tmp_path / "external.py"
        cli.rename(external)
        if change == "symlink_file":
            cli.symlink_to(external)
        else:
            cli.hardlink_to(external)
    elif change in {"symlink_directory", "source_alias"}:
        directory = root / "src" if change == "source_alias" else cli.parent
        external = tmp_path / "external"
        directory.rename(external)
        directory.symlink_to(external, target_is_directory=True)
    elif change == "fifo":
        cli.unlink()
        os.mkfifo(cli)
    elif change == "root_alias":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif change == "root_loop":
        root = tmp_path / "loop"
        root.symlink_to(root)
    elif change == "manifest_alias":
        alias = tmp_path / "manifest-alias"
        alias.symlink_to(manifest)
        manifest = alias
    elif change == "wrong_anchor":
        anchor = "0" * 64
    elif change == "bad_json":
        raw = "{"
    elif change == "duplicate_key":
        raw = manifest.read_text().replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
    elif change in {"absolute", "traversal", "dot_path", "backslash"}:
        key = {
            "absolute": "/outside.py",
            "traversal": "../outside.py",
            "dot_path": "./outside.py",
            "backslash": "agent_harness\\outside.py",
        }[change]
        data["files"][key] = "a" * 64
    elif change == "bad_digest":
        data["files"]["agent_harness/cli.py"] = "invalid"
    elif change == "wrong_root":
        data["runtime_root"] = str(tmp_path)
    elif change == "version":
        data["schema_version"] = True
    elif change == "unknown_field":
        data["unknown"] = "payload"
    elif change in {"wrong_python", "wrong_bootstrap"}:
        data[change.removeprefix("wrong_")]["sha256"] = "0" * 64
    elif change == "empty_inventory":
        data["files"] = {}
    elif change == "too_many_files":
        data["files"] = {f"{i}.py": "a" * 64 for i in range(10001)}
    elif change == "oversized_manifest":
        raw = " " * (2 * 1024 * 1024 + 1)
    if change != "manifest_alias":
        manifest.write_text(raw if raw is not None else json.dumps(data))
    result = dispatch(root, manifest, anchor=anchor)
    assert result.returncode == 78, result.stderr
    assert result.stderr.strip() == "verified runtime integrity check failed"
    assert not marker.exists()
    assert not (root / "state").exists()


def test_isolated_bootstrap_ignores_startup_poisoning(tmp_path: Path) -> None:
    root, manifest, marker = bundle(tmp_path)
    poison = tmp_path / "poison"
    poison.mkdir()
    poisoned = tmp_path / "poisoned"
    payload = f"from pathlib import Path; Path({str(poisoned)!r}).touch()\n"
    (poison / "sitecustomize.py").write_text(payload)
    (poison / "usercustomize.py").write_text(payload)
    (poison / "startup.pth").write_text("import sitecustomize\n")
    (poison / "agent_harness").mkdir()
    (poison / "agent_harness/__init__.py").write_text(payload)
    env = dict(os.environ, PYTHONPATH=str(poison), PYTHONHOME=str(poison))
    result = dispatch(root, manifest, env=env)
    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert not poisoned.exists()


def test_launcher_requires_isolation_before_import(tmp_path: Path) -> None:
    root, manifest, marker = bundle(tmp_path)
    result = dispatch(root, manifest, flags=("-I", "-B"))
    assert result.returncode == 78
    assert not marker.exists()


@pytest.mark.parametrize(
    "mode,turns,status",
    [("default", 1, "human"), ("zero", 1, "human"), ("correct", 2, "pass")],
)
def test_verified_real_package_keeps_fixture_retry_and_diagnostics(
    tmp_path: Path, mode: str, turns: int, status: str
) -> None:
    from test_cli_retries import retry_fixture
    from test_run_config import write_config

    from agent_harness.journal import EventJournal

    root, manifest, _ = bundle(tmp_path)
    shutil.rmtree(root / "src")
    shutil.copytree(
        SCRIPT.parents[1] / "src",
        root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "AGENTS.md"),
    )
    # __init__ reads its version through stdlib importlib.metadata, even under -S.
    from importlib.metadata import distribution

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
    manifest.write_text(json.dumps(frozen, sort_keys=True))
    config_dir = tmp_path / "configuration"
    config_dir.mkdir()
    config, data, checker = retry_fixture(config_dir, mode)
    counter = tmp_path / "fixture-turns"
    executable = tmp_path / "fixture-native"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\nsys.stdin.buffer.read()\n"
        f"counter = Path({str(counter)!r})\n"
        "previous = counter.read_text() if counter.exists() else ''\n"
        "counter.write_text(previous + 'x')\n"
        "Path('value.txt').write_text('after' if previous else 'broken')\n"
        "Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('fixture')\n"
        'print(\'{"type":"item.completed","item":{"type":"command_execution","status":"completed","exit_code":0,"command":"SECRET"}}\')\n'
    )
    executable.chmod(0o700)
    data["backend"] = "codex"
    data["executable"] = str(executable)
    approval = write_config(config, data)
    preview = dispatch(
        root, manifest, arguments=("run", "--preview", "--config", str(config))
    )
    assert preview.returncode == 0, preview.stderr
    assert not counter.exists()
    assert not Path(data["state_root"]).exists()
    outcome = dispatch(
        root,
        manifest,
        arguments=(
            "run",
            "--config",
            str(config),
            "--approve-config-sha256",
            approval,
            "--approver",
            "fixture-human",
        ),
        timeout=45,
    )
    assert outcome.returncode == (0 if status == "pass" else 1), outcome.stderr
    saved = json.loads(outcome.stdout)
    assert saved["status"] == status
    assert counter.read_text() == "x" * turns
    assert (Path(data["repository"]) / "check.py").read_bytes() == checker
    journal = EventJournal(Path(saved["state_dir"]))
    observations = [e for e in journal.read() if e["event"] == "turn_observation"]
    assert len(observations) == turns
    for observation in observations:
        diagnostic = observation["diagnostics"]
        assert isinstance(diagnostic, dict)
        assert diagnostic["events"][0]["exit_code"] == 0
        assert "SECRET" not in json.dumps(diagnostic)
    before = journal.path.read_bytes()
    historical = dispatch(
        root, manifest, arguments=("status", "--state-dir", saved["state_dir"])
    )
    assert historical.returncode == 0, historical.stderr
    assert json.loads(historical.stdout)["recorded_status"] == status
    assert journal.path.read_bytes() == before
    assert counter.read_text() == "x" * turns


@pytest.mark.parametrize(
    "kind", ["altered_metadata", "second_distribution", "dist_info_python", "pth"]
)
def test_guard_keeps_metadata_exception_narrow(tmp_path: Path, kind: str) -> None:
    root, manifest, marker = bundle(tmp_path)
    data = json.loads(manifest.read_text())
    metadata = root / "src/agent_harness-0.1.0.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Metadata-Version: 2.4\nName: agent-harness\nVersion: 0.1.0\n")
    data["files"][metadata.relative_to(root / "src").as_posix()] = digest(metadata)
    if kind == "altered_metadata":
        metadata.write_text("Version: altered\n")
    else:
        extra = (
            root
            / "src"
            / {
                "second_distribution": "agent_harness-9.9.dist-info/METADATA",
                "dist_info_python": "agent_harness-0.1.0.dist-info/unchecked.py",
                "pth": "startup.pth",
            }[kind]
        )
        extra.parent.mkdir(exist_ok=True)
        extra.write_text("unchecked")
        data["files"][extra.relative_to(root / "src").as_posix()] = digest(extra)
    manifest.write_text(json.dumps(data))
    result = dispatch(root, manifest)
    assert result.returncode == 78
    assert result.stderr.strip() == "verified runtime integrity check failed"
    assert not marker.exists()

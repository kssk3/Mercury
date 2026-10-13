"""Exercise the installed console script, including packaging and process exits."""

import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

import agent_harness


@pytest.fixture(scope="module")
def entrypoint() -> Path:
    candidate = Path(__file__).resolve().parents[1]
    environment = candidate / ".venv"
    assert Path(sys.prefix).resolve() == environment.resolve(), (
        "Run with the candidate's uv environment; foreign environments are invalid"
    )
    assert Path(agent_harness.__file__).resolve().is_relative_to(candidate / "src")
    package = distribution("agent-harness")
    installation = package.read_text("direct_url.json")
    assert installation is not None, "Install the candidate with uv sync --locked --dev"
    assert json.loads(installation)["url"] == candidate.as_uri()
    executable = environment / "bin" / "local-harness"
    assert executable.is_file(), "Candidate console entry point is missing"
    assert os.access(executable, os.X_OK), (
        "Candidate console entry point is not executable"
    )
    assert executable.resolve().is_relative_to(environment.resolve()), (
        "Console entry point must not resolve outside the candidate environment"
    )
    assert any(
        point.group == "console_scripts"
        and point.name == "local-harness"
        and point.value == "agent_harness.cli:main"
        for point in package.entry_points
    ), "Candidate package must provide the promised console entry point"
    return executable


def process_environment() -> dict[str, str]:
    # Keep the subprocess independent of ambient Git selection and Python imports.
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }


def run_cli(
    entrypoint: Path, cwd: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(entrypoint), *arguments],
        cwd=cwd,
        env=process_environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def snapshot(repository: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repository).as_posix(): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository with spaces"
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        env=process_environment(),
        capture_output=True,
        check=True,
        timeout=10,
    )
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("original\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "src/main.py", ".gitignore"],
        env=process_environment(),
        capture_output=True,
        check=True,
        timeout=10,
    )
    (root / "src" / "main.py").write_text("dirty\n", encoding="utf-8")
    (root / "README.md").write_text("untracked\n", encoding="utf-8")
    (root / "ignored.py").write_text("ignored\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("operation", ["--version", "--help"])
def test_installed_version_and_help(
    entrypoint: Path, tmp_path: Path, operation: str
) -> None:
    result = run_cli(entrypoint, tmp_path, operation)

    assert result.returncode == 0
    assert result.stderr == ""
    if operation == "--version":
        assert (
            result.stdout == f"local-harness {distribution('agent-harness').version}\n"
        )
    else:
        assert "usage: local-harness" in result.stdout
        assert "--version" in result.stdout
        assert "profile" in result.stdout


@pytest.mark.parametrize(
    "explicit_path", [False, True], ids=["default", "explicit-file"]
)
def test_installed_profile_json_and_preserved_repository(
    entrypoint: Path, repository: Path, tmp_path: Path, explicit_path: bool
) -> None:
    before = snapshot(repository)
    arguments = (
        ("profile", str(repository / "src" / "main.py"))
        if explicit_path
        else ("profile",)
    )
    cwd = tmp_path if explicit_path else repository / "src"

    result = run_cli(entrypoint, cwd, *arguments)

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "repository_root": str(repository.resolve()),
        "file_count": 3,
        "languages": ["Python"],
        "manifests": [],
        "documents": ["README.md"],
        "top_level_directories": ["src"],
    }
    assert result.stdout.count("\n") == 1
    assert snapshot(repository) == before


def test_installed_profile_rejects_non_git_path(
    entrypoint: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    before = snapshot(outside)

    result = run_cli(entrypoint, tmp_path, "profile", str(outside))

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Git working tree" in result.stderr
    assert "Traceback" not in result.stderr
    assert snapshot(outside) == before

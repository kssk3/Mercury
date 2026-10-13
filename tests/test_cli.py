import json
import re
import subprocess
from pathlib import Path

import pytest

from agent_harness.cli import main


def test_version_operation_reports_cli_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert re.fullmatch(r"local-harness \d+\.\d+\.\d+\n", captured.out)
    assert captured.err == ""


def test_profile_command_prints_json_for_default_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path / "repository")], check=True)
    repository = tmp_path / "repository"
    (repository / "README.md").write_text("content", encoding="utf-8")
    monkeypatch.chdir(repository)

    exit_code = main(["profile"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "repository_root": str(repository.resolve()),
        "file_count": 1,
        "languages": [],
        "manifests": [],
        "documents": ["README.md"],
        "top_level_directories": [],
    }
    assert captured.out.count("\n") == 1


def test_profile_command_reports_invalid_path_without_traceback_or_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    exit_code = main(["profile", str(outside)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "Git working tree" in captured.err
    assert "Traceback" not in captured.err


def test_help_still_lists_version_and_profile(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert "--version" in captured.out
    assert "profile" in captured.out

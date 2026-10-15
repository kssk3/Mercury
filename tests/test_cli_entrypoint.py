"""Exercise the installed console script, including packaging and process exits."""

import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

import agent_harness
import agent_harness.cli as cli


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
        assert "contract" in result.stdout
        assert "plan" in result.stdout


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


def test_contract_preview_prints_valid_contract_json(
    entrypoint: Path, repository: Path
) -> None:
    before = snapshot(repository)

    result = run_cli(
        entrypoint,
        repository,
        "contract",
        "--goal",
        "Fix the total",
        "--allow",
        "missing/cart.py",
        "--protect",
        "missing/test_cart.py",
        "--criterion",
        "The total is correct",
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        '{"allowed_paths":["missing/cart.py"],'
        '"completion_criteria":["The total is correct"],'
        '"goal":"Fix the total",'
        '"protected_paths":["missing/test_cart.py"]}\n'
    )
    assert snapshot(repository) == before


def test_contract_preview_preserves_repeated_unicode_values_without_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse_execution(*args: object, **kwargs: object) -> None:
        pytest.fail("Contract preview attempted repository profiling or a subprocess")

    monkeypatch.setattr(cli, "build_repository_profile", refuse_execution)
    monkeypatch.setattr(subprocess, "Popen", refuse_execution)
    code = cli.main(
        [
            "contract",
            "--goal",
            "  계산 고치기  ",
            "--allow",
            "새 파일.py",
            "--allow",
            "src/cafe\u0301.py",
            "--protect",
            "검사.py",
            "--protect",
            "docs/spec.md",
            "--criterion",
            "  값 확인  ",
            "--criterion",
            "문자 확인",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "goal": "  계산 고치기  ",
        "allowed_paths": ["새 파일.py", "src/cafe\u0301.py"],
        "protected_paths": ["검사.py", "docs/spec.md"],
        "completion_criteria": ["  값 확인  ", "문자 확인"],
    }


def test_contract_preview_defaults_to_empty_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["contract", "--goal", "Review", "--criterion", "Read it"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "goal": "Review",
        "allowed_paths": [],
        "protected_paths": [],
        "completion_criteria": ["Read it"],
    }


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (["--goal", " "], "goal"),
        (["--criterion", ""], "criterion"),
        (["--allow", "/absolute.py"], "relative"),
        (["--protect", "../outside.py"], "parent traversal"),
        (["--allow", "nested/.GiT/config"], "Git metadata"),
        (["--protect", "src//file.py"], "canonical"),
        (["--allow", "src/A.py", "--allow", "src/a.py"], "duplicate"),
        (["--protect", "checks.py", "--protect", "checks.py"], "duplicate"),
        (["--allow", "src", "--protect", "src/check.py"], "both allowed and protected"),
    ],
)
def test_contract_preview_reuses_field_and_path_refusals(
    arguments: list[str], reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["contract", "--goal", "Fix", "--criterion", "Checked", *arguments])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert reason in captured.err
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["--criterion", "Checked"],
        ["--goal", "Fix"],
    ],
)
def test_contract_preview_requires_goal_and_criterion(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        cli.main(["contract", *arguments])
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "required" in captured.err
    assert "Traceback" not in captured.err


def test_installed_contract_refusal_preserves_repository(
    entrypoint: Path, repository: Path
) -> None:
    before = snapshot(repository)
    result = run_cli(
        entrypoint,
        repository,
        "contract",
        "--goal",
        "Fix",
        "--criterion",
        "Checked",
        "--allow",
        ".git/config",
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Git metadata" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr.count("\n") == 1
    assert snapshot(repository) == before


def verification_plan_arguments() -> list[str]:
    return [
        "plan",
        "--goal",
        "Fix the total",
        "--allow",
        "missing/cart.py",
        "--protect",
        "missing/test_cart.py",
        "--criterion",
        "The total is correct",
        "--command",
        '["python", "check.py"]',
        "--timeout-seconds",
        "10",
        "--criterion-map",
        '{"The total is correct": [["python", "check.py"]]}',
    ]


def test_installed_verification_plan_json_preserves_repository(
    entrypoint: Path, repository: Path
) -> None:
    before = snapshot(repository)

    result = run_cli(entrypoint, repository, *verification_plan_arguments())

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert json.loads(result.stdout) == {
        "contract": {
            "goal": "Fix the total",
            "allowed_paths": ["missing/cart.py"],
            "protected_paths": ["missing/test_cart.py"],
            "completion_criteria": ["The total is correct"],
        },
        "verification_policy": {
            "commands": [["python", "check.py"]],
            "working_directory": ".",
            "timeout_seconds": 10,
        },
        "criterion_bindings": [
            {"criterion": "The total is correct", "commands": [["python", "check.py"]]}
        ],
    }
    assert snapshot(repository) == before


def test_verification_plan_preserves_exact_strings_without_io_or_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ordinary package loading is allowed; preview itself must remain pure.
    __import__("agent_harness.loop")

    def refuse_io(*args: object, **kwargs: object) -> None:
        pytest.fail("Verification plan attempted filesystem access or execution")

    monkeypatch.setattr(cli, "build_repository_profile", refuse_io)
    monkeypatch.setattr(subprocess, "Popen", refuse_io)
    monkeypatch.setattr(Path, "open", refuse_io)
    monkeypatch.setattr(os, "open", refuse_io)
    monkeypatch.setattr(os, "scandir", refuse_io)
    first = [
        "nonexistent executable",
        "  값  ",
        "$(touch sentinel)",
        "a;b",
        "cafe\u0301",
    ]
    second = ["other missing executable", "--flag=value with spaces", "한글"]
    code = cli.main(
        [
            "plan",
            "--goal",
            "  계산 고치기  ",
            "--allow",
            "새 파일.py",
            "--allow",
            "src/cafe\u0301.py",
            "--protect",
            "검사.py",
            "--protect",
            "docs/spec.md",
            "--criterion",
            "  값 확인  ",
            "--criterion",
            "문자 확인",
            "--command",
            json.dumps(first),
            "--command",
            json.dumps(second),
            "--working-directory",
            "missing directory/새 폴더",
            "--timeout-seconds",
            "7",
            "--criterion-map",
            json.dumps({"문자 확인": [second], "  값 확인  ": [second, first]}),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "contract": {
            "goal": "  계산 고치기  ",
            "allowed_paths": ["새 파일.py", "src/cafe\u0301.py"],
            "protected_paths": ["검사.py", "docs/spec.md"],
            "completion_criteria": ["  값 확인  ", "문자 확인"],
        },
        "verification_policy": {
            "commands": [first, second],
            "working_directory": "missing directory/새 폴더",
            "timeout_seconds": 7,
        },
        "criterion_bindings": [
            {"criterion": "  값 확인  ", "commands": [second, first]},
            {"criterion": "문자 확인", "commands": [second]},
        ],
    }


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (["--command", "["], "JSON"),
        (["--command", '"python check.py"'], "array"),
        (["--command", '{"command": "python"}'], "array"),
        (["--command", "null"], "array"),
        (["--command", "[]"], "command"),
        (["--command", '["python", ""]'], "command"),
        (["--command", '["python", 1]'], "command"),
        (["--command", '["python", true]'], "command"),
        (["--command", '[["python"]]'], "command"),
        (["--command", '["python", "\\u0000"]'], "command"),
        (["--command", "[" * 2000 + "]" * 2000], "local-harness:"),
        (["--working-directory", "/absolute"], "relative"),
        (["--working-directory", "../outside"], "parent traversal"),
        (["--working-directory", "nested/.GiT"], "Git metadata"),
        (["--working-directory", "bad\x00directory"], "NUL"),
        (["--timeout-seconds", "0"], "positive integer"),
        (["--timeout-seconds", "-1"], "positive integer"),
        (["--goal", " "], "goal"),
        (["--criterion", ""], "criterion"),
        (["--allow", "../outside"], "parent traversal"),
        (["--allow", "src", "--protect", "src/test.py"], "both allowed and protected"),
        (["--criterion-map", "{"], "JSON"),
        (["--criterion-map", "[" * 2000 + "]" * 2000], "local-harness:"),
        (["--criterion-map", "[]"], "invalid_criterion_mapping"),
        (["--criterion-map", "{}"], "invalid_criterion_mapping"),
        (
            ["--criterion-map", '{"Other": [["python", "check.py"]]}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": [], "Other": []}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": []}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": "python check.py"}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": ["python", "check.py"]}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": [["python", "other.py"]]}'],
            "invalid_criterion_mapping",
        ),
        (
            ["--criterion-map", '{"The total is correct": [["python", "check.py "]]}'],
            "invalid_criterion_mapping",
        ),
        (["--criterion", "The total is correct"], "invalid_criterion_mapping"),
        (["--criterion", "Missing"], "invalid_criterion_mapping"),
        (
            [
                "--criterion-map",
                '{"The total is correct": [], "The total is correct": [["python", "check.py"]]}',
            ],
            "duplicate JSON key",
        ),
    ],
)
def test_verification_plan_refuses_invalid_input(
    arguments: list[str],
    reason: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    __import__("agent_harness.loop")

    def refuse_io(*args: object, **kwargs: object) -> None:
        pytest.fail(
            "Invalid verification plan attempted filesystem access or execution"
        )

    monkeypatch.setattr(cli, "build_repository_profile", refuse_io)
    monkeypatch.setattr(subprocess, "Popen", refuse_io)
    monkeypatch.setattr(Path, "open", refuse_io)
    monkeypatch.setattr(os, "open", refuse_io)
    monkeypatch.setattr(os, "scandir", refuse_io)
    code = cli.main([*verification_plan_arguments(), *arguments])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert reason in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "missing",
    ["--goal", "--criterion", "--command", "--timeout-seconds", "--criterion-map"],
)
def test_verification_plan_requires_its_inputs(
    missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = verification_plan_arguments()
    index = arguments.index(missing)
    del arguments[index : index + 2]
    with pytest.raises(SystemExit) as failure:
        cli.main(arguments)
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "required" in captured.err
    assert "Traceback" not in captured.err


def test_verification_plan_requires_integer_timeout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as failure:
        cli.main([*verification_plan_arguments(), "--timeout-seconds", "1.5"])
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--timeout-seconds" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--command", '["python", ""]'],
        [
            "--criterion-map",
            '{"The total is correct": [], "The total is correct": [["python", "check.py"]]}',
        ],
        ["--working-directory", "../outside"],
    ],
)
def test_installed_verification_plan_refusal_preserves_repository(
    entrypoint: Path, repository: Path, arguments: list[str]
) -> None:
    before = snapshot(repository)
    result = run_cli(entrypoint, repository, *verification_plan_arguments(), *arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr
    assert "Traceback" not in result.stderr
    assert snapshot(repository) == before

"""Committed-tree classification contracts using real Git repositories."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_changes.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def repository_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("ci-changes-repository").resolve()
    git(root, "init", "-q")
    # A copied seed must not outlive a detached Git housekeeping writer.
    git(root, "config", "--local", "maintenance.auto", "false")
    git(root, "config", "user.email", "ci3@example.invalid")
    git(root, "config", "user.name", "CI3 fixture")
    git(root, "config", "core.autocrlf", "false")
    git(root, "commit", "--allow-empty", "-qm", "base")
    return root


@pytest.fixture
def repo(tmp_path: Path, repository_seed: Path) -> Path:
    shutil.copytree(repository_seed, tmp_path, dirs_exist_ok=True)
    return tmp_path


def commit_files(repo: Path, *paths: str) -> str:
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "change")
    return git(repo, "rev-parse", "HEAD")


def run_classifier(
    repo: Path, base: str, head: str, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, "--head", head, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
        env={key: value for key, value in os.environ.items() if key != "GITHUB_OUTPUT"},
        check=False,
    )


def decision(repo: Path, base: str, head: str) -> dict[str, Any]:
    result = run_classifier(repo, base, head)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    value: dict[str, Any] = json.loads(result.stdout)
    assert value["base"] == base
    assert value["head"] == head
    assert value["macos_required"] is (not value["record_only"])
    return value


def workflow_command(step: str) -> str:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    static = workflow.split("  tests-linux:", 1)[0]
    body = static.split(f"      - name: {step}\n", 1)[1].split("      - name:", 1)[0]
    command = body.split("        run: ", 1)[1]
    if command.startswith("|\n"):
        return "\n".join(
            line.removeprefix("          ") for line in command[2:].splitlines()
        )
    return command.strip()


def run_workflow_step(
    repo: Path,
    step: str,
    base: str,
    head: str,
    *,
    event: str = "push",
    verification: str = "full",
) -> subprocess.CompletedProcess[str]:
    script = repo / "scripts/ci_changes.py"
    script.parent.mkdir(exist_ok=True)
    shutil.copyfile(SCRIPT, script)
    return subprocess.run(
        ["bash", "-e", "-c", workflow_command(step)],
        cwd=repo,
        env=dict(
            os.environ,
            CHANGE_EVENT=event,
            CHANGE_BASE=base,
            CHANGE_HEAD=head,
            CHANGE_VERIFICATION=verification,
            GITHUB_OUTPUT=str(repo / "github-output"),
            UV_PYTHON=sys.executable,
            UV_OFFLINE="1",
            UV_CACHE_DIR=str(repo / ".uv-cache"),
        ),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_initial_push_requires_full_gates(repo: Path) -> None:
    head = commit_files(repo, "README.md")

    result = run_workflow_step(
        repo, "Classify exact event base and head", "0" * 40, head
    )

    assert result.returncode == 0, result.stderr
    assert (repo / "github-output").read_text() == (
        "record_only=false\nmacos_required=true\nverification=full\n"
        "state_required=true\n"
    )


@pytest.mark.parametrize("bad_whitespace", [False, True])
def test_initial_push_checks_whitespace_in_the_complete_tree(
    repo: Path, bad_whitespace: bool
) -> None:
    # The problem is in an older file, outside the latest commit's diff.
    path = repo / "README.md"
    path.write_text("old text \n" if bad_whitespace else "old text\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "older document")
    head = commit_files(repo, "docs/quickstart.md")

    result = run_workflow_step(repo, "Document diff checks", "0" * 40, head)

    assert (result.returncode != 0) is bad_whitespace, result.stderr
    if bad_whitespace:
        assert "README.md:1: trailing whitespace" in result.stdout


@pytest.mark.parametrize(
    "invalid", ["HEAD", "a" * 39, "A" * 40, "a" * 40 + "\n", "-bad", "f" * 40]
)
@pytest.mark.parametrize("endpoint", ["base", "head"])
def test_workflow_never_bootstraps_malformed_or_unavailable_refs(
    repo: Path, invalid: str, endpoint: str
) -> None:
    head = commit_files(repo, "README.md")
    base = invalid if endpoint == "base" else "0" * 40
    head = invalid if endpoint == "head" else head

    result = run_workflow_step(repo, "Classify exact event base and head", base, head)

    assert result.returncode != 0
    assert not (repo / "github-output").exists()


def test_zero_base_pull_request_remains_invalid(repo: Path) -> None:
    head = commit_files(repo, "README.md")

    result = run_workflow_step(
        repo, "Classify exact event base and head", "0" * 40, head, event="pull_request"
    )

    assert result.returncode != 0
    assert not (repo / "github-output").exists()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "README.md",
            "record_only=true\nmacos_required=false\nverification=document\nstate_required=false\n",
        ),
        (
            ".project-state/current.json",
            "record_only=true\nmacos_required=false\nverification=state\nstate_required=true\n",
        ),
        (
            "tests/fixtures/project-state-v1.json",
            "record_only=false\nmacos_required=true\nverification=full\nstate_required=true\n",
        ),
    ],
)
def test_normal_workflow_keeps_exact_classifier_routes(
    repo: Path, path: str, expected: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, path)

    result = run_workflow_step(repo, "Classify exact event base and head", base, head)

    assert result.returncode == 0, result.stderr
    assert (repo / "github-output").read_text() == expected


@pytest.mark.parametrize(
    ("verification", "state_input", "accepted"),
    [
        ("state", "valid", True),
        ("state", "invalid", False),
        ("state", "missing", False),
        ("full", "valid", True),
        ("full", "invalid", False),
        ("full", "missing", True),
    ],
)
def test_schema_workflow_validates_current_state_as_well_as_public_fixture(
    repo: Path, verification: str, state_input: str, accepted: bool
) -> None:
    for name in (
        "schemas/project-state-v1.schema.json",
        "tests/fixtures/project-state-v1.json",
    ):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    current = repo / ".project-state/current.json"
    if state_input != "missing":
        current.parent.mkdir()
        current.write_bytes(
            (ROOT / "tests/fixtures/project-state-v1.json").read_bytes()
            if state_input == "valid"
            else b"{}"
        )
        git(repo, "add", ".project-state/current.json")
        git(repo, "commit", "-qm", "synthetic current state")
    revision = git(repo, "rev-parse", "HEAD")

    result = run_workflow_step(
        repo, "schema-validation", revision, revision, verification=verification
    )

    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert ".project-state/current.json" in result.stderr


def test_ci_has_a_classification_gate_before_conditional_linux_tests() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "scripts/ci_changes.py" in workflow, (
        "CI has no changed-path classification gate"
    )
    assert "tests-linux:" in workflow
    assert "needs: static-checks" in workflow
    assert "needs.static-checks.result == 'success'" in workflow
    assert "record_only != 'true'" in workflow
    assert "tests-macos:" not in workflow
    assert "macos-" not in workflow


@pytest.mark.parametrize(
    "path",
    [
        "docs/work-units/CI3-report.md",
        "docs/work-units/CI3-summary.md",
        "docs/work-units/space and Unicode 한글-report.md",
        "docs/work-units/한글-summary.md",
    ],
)
def test_nonempty_direct_report_and_summary_changes_allow_skip(
    repo: Path, path: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, path)
    value = decision(repo, base, head)
    assert value["record_only"] is True
    assert value["changed_paths"] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "docs/work-units/.hidden-report.md",
        "uv.lock",
        "schemas/project-state-v1.schema.json",
        "src/app.py",
        "tests/test_app.py",
        ".github/workflows/ci.yml",
        "unknown-report.md",
        "docs/work-units/bad\nname-report.md",
        "docs/work-units/back\\slash-report.md",
    ],
)
def test_every_non_allowlisted_path_requires_full_verification(
    repo: Path, path: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, path)
    assert decision(repo, base, head)["record_only"] is False


def test_mixed_record_and_source_change_requires_full_verification(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, "docs/work-units/CI3-report.md", "src/app.py")
    value = decision(repo, base, head)
    assert value["record_only"] is False
    assert set(value["changed_paths"]) == {
        "docs/work-units/CI3-report.md",
        "src/app.py",
    }


@pytest.mark.parametrize(
    ("old", "new", "record_only"),
    [
        ("src/app.py", "docs/work-units/CI3-report.md", False),
        ("docs/work-units/CI3-report.md", "src/app.py", False),
        ("docs/work-units/old-report.md", "docs/work-units/new-summary.md", True),
    ],
)
def test_rename_classification_includes_both_endpoints(
    repo: Path,
    old: str,
    new: str,
    record_only: bool,
) -> None:
    base = commit_files(repo, old)
    destination = repo / new
    destination.parent.mkdir(parents=True, exist_ok=True)
    (repo / old).rename(destination)
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "rename")
    head = git(repo, "rev-parse", "HEAD")
    value = decision(repo, base, head)
    assert set(value["changed_paths"]) == {old, new}
    assert value["record_only"] is record_only


@pytest.mark.parametrize(
    ("path", "record_only"),
    [
        ("docs/work-units/CI3-report.md", True),
        ("src/app.py", False),
    ],
)
def test_deleted_path_still_influences_classification(
    repo: Path,
    path: str,
    record_only: bool,
) -> None:
    base = commit_files(repo, path)
    (repo / path).unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "delete")
    value = decision(repo, base, git(repo, "rev-parse", "HEAD"))
    assert value["changed_paths"] == [path]
    assert value["record_only"] is record_only


def test_empty_diff_requires_full_verification(repo: Path) -> None:
    revision = git(repo, "rev-parse", "HEAD")
    value = decision(repo, revision, revision)
    assert value["changed_paths"] == []
    assert value["record_only"] is False


@pytest.mark.parametrize(
    "invalid", ["HEAD", "a" * 39, "A" * 40, "a" * 40 + "\n", "-bad", "0" * 40, "f" * 40]
)
def test_invalid_or_missing_revision_never_emits_skip(
    repo: Path,
    tmp_path: Path,
    invalid: str,
) -> None:
    output = tmp_path / "github-output"
    result = run_classifier(
        repo, invalid, git(repo, "rev-parse", "HEAD"), "--github-output", str(output)
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_non_commit_object_is_not_revision_evidence(repo: Path) -> None:
    head = commit_files(repo, "docs/work-units/CI3-report.md")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    result = run_classifier(repo, tree, head)
    assert result.returncode != 0
    assert result.stdout == ""


def test_non_git_directory_fails_without_success_output(tmp_path: Path) -> None:
    result = run_classifier(tmp_path, "a" * 40, "b" * 40)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_github_outputs_preserve_booleans_and_add_fixed_routing_values(
    repo: Path,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, "docs/work-units/space and Unicode 한글-report.md")
    output = repo / "outputs"
    result = run_classifier(repo, base, head, "--github-output", str(output))
    assert result.returncode == 0, result.stderr
    assert output.read_text() == (
        "record_only=true\nmacos_required=false\nverification=document\n"
        "state_required=false\n"
    )


def test_output_write_failure_does_not_emit_success_json(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, "docs/work-units/CI3-report.md")
    result = run_classifier(repo, base, head, "--github-output", str(repo))
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "docs/work-units/../CI3-report.md",
        "docs//work-units/CI3-report.md",
        "./docs/work-units/CI3-report.md",
        "/docs/work-units/CI3-report.md",
        "docs/work-units/./CI3-report.md",
        "docs/work-units/-report.md",
        "docs/work-units/bad\x00-report.md",
        "docs/work-units/bad\r-report.md",
        "docs/work-units/bad\udcff-report.md",
    ],
)
def test_unsafe_raw_path_cannot_qualify_for_record_only(path: str) -> None:
    predicate = cast(
        Callable[[str], bool], runpy.run_path(str(SCRIPT))["is_record_path"]
    )
    assert predicate(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "AGENTS.md",
        ".github/pull_request_template.md",
        "docs/project/WORKFLOW.md",
        "docs/work-units/TEMPLATE.md",
        "docs/work-units/nested/CI4-report.md",
        "docs/space and 한글/document.md",
        ".agents/skills/harness-tdd/SKILL.md",
        ".agents/skills/team/nested/SKILL.md",
    ],
)
def test_known_safe_markdown_needs_only_document_checks(repo: Path, path: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    value = decision(repo, base, commit_files(repo, path))
    assert value["verification"] == "document"
    assert value["record_only"] is True
    assert value["state_required"] is False


@pytest.mark.parametrize("with_document", [False, True])
def test_current_state_requires_schema_without_application_checks(
    repo: Path, with_document: bool
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    paths = [".project-state/current.json"]
    if with_document:
        paths.append("docs/project/STATUS.md")
    value = decision(repo, base, commit_files(repo, *paths))
    assert value["verification"] == "state"
    assert value["record_only"] is True
    assert value["state_required"] is True


@pytest.mark.parametrize(
    "path",
    [
        "docs/work-units/bad\tname-report.md",
        "docs/.hidden/doc.md",
        "docs/nested/.hidden.md",
        ".agents/skills/.hidden/SKILL.md",
        ".agents/skills/team/README.md",
        ".agents/skills/SKILL.md",
        ".github/other.md",
        "OTHER.md",
        "docs/file.json",
        ".project-state/other.json",
        "scripts/ci_changes.py",
        "pyproject.toml",
        "docs/bad\x85name.md",
    ],
)
def test_unknown_or_unsafe_location_never_exempts_full_gate(
    repo: Path, path: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    value = decision(repo, base, commit_files(repo, path))
    assert value["verification"] == "full"
    assert value["record_only"] is False
    assert value["state_required"] is True


@pytest.mark.parametrize("path", ["README.md", ".project-state/current.json"])
def test_special_tree_entry_at_allowed_location_requires_full_gate(
    repo: Path, path: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to("../outside")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "symlink")
    assert (
        decision(repo, base, git(repo, "rev-parse", "HEAD"))["verification"] == "full"
    )


def test_replacing_special_document_with_regular_file_is_still_full(repo: Path) -> None:
    target = repo / "README.md"
    target.symlink_to("outside")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "symlink")
    base = git(repo, "rev-parse", "HEAD")
    target.unlink()
    head = commit_files(repo, "README.md")
    assert decision(repo, base, head)["verification"] == "full"


@pytest.mark.parametrize(
    ("old", "new", "verification"),
    [
        ("README.md", "docs/한글 renamed.md", "document"),
        ("README.md", ".project-state/current.json", "state"),
        ("README.md", "OTHER.md", "full"),
    ],
)
def test_both_rename_endpoints_control_proportional_gate(
    repo: Path, old: str, new: str, verification: str
) -> None:
    base = commit_files(repo, old)
    destination = repo / new
    destination.parent.mkdir(parents=True, exist_ok=True)
    (repo / old).rename(destination)
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "rename")
    value = decision(repo, base, git(repo, "rev-parse", "HEAD"))
    assert set(value["changed_paths"]) == {old, new}
    assert value["verification"] == verification


@pytest.mark.parametrize("path", ["README.md", ".project-state/current.json"])
def test_deleted_allowed_file_keeps_required_proportional_check(
    repo: Path, path: str
) -> None:
    base = commit_files(repo, path)
    (repo / path).unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "delete")
    value = decision(repo, base, git(repo, "rev-parse", "HEAD"))
    assert value["record_only"] is True
    assert value["state_required"] is (path == ".project-state/current.json")


def test_state_mixed_with_execution_config_requires_full_gate(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    value = decision(
        repo, base, commit_files(repo, ".project-state/current.json", "uv.lock")
    )
    assert value["verification"] == "full"
    assert value["record_only"] is False


@pytest.mark.parametrize(
    "path",
    [
        "docs/../README.md",
        "docs//README.md",
        "./README.md",
        "docs/a\\b.md",
        "docs/a\x00.md",
        "docs/a\udcff.md",
        "docs/a\u200b.md",
    ],
)
def test_unsafe_raw_document_path_never_qualifies(path: str) -> None:
    predicate = cast(
        Callable[[str], bool], runpy.run_path(str(SCRIPT))["is_document_path"]
    )
    assert predicate(path) is False


def test_incomplete_git_path_output_raises_evidence_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real Git cannot produce truncated -z evidence; replace only this boundary.
    namespace = runpy.run_path(str(SCRIPT))
    classify = cast(Callable[[str, str], dict[str, object]], namespace["classify"])
    monkeypatch.setitem(
        classify.__globals__, "resolve_commit", lambda revision: revision
    )
    monkeypatch.setitem(classify.__globals__, "git_bytes", lambda *args: b"README.md")
    with pytest.raises(ValueError, match="NUL-delimited"):
        classify("a" * 40, "b" * 40)


@pytest.mark.parametrize(
    ("step", "condition"),
    [
        (
            "Install locked development dependencies",
            "steps.changes.outputs.verification != 'document'",
        ),
        ("schema-validation", "steps.changes.outputs.state_required == 'true'"),
        ("Ruff lint", "steps.changes.outputs.verification == 'full'"),
        ("Ruff format", "steps.changes.outputs.verification == 'full'"),
        ("Mypy", "steps.changes.outputs.verification == 'full'"),
    ],
)
def test_static_steps_are_gated_by_classifier_outputs(
    step: str, condition: str
) -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    static = workflow.split("  tests-linux:", 1)[0]
    step_body = static.split(f"      - name: {step}\n", 1)[1].split("      - name:", 1)[
        0
    ]
    assert f"if: ${{{{ {condition} }}}}" in step_body
    classification = static.index("      - name: Classify exact event base and head")
    install = static.index("      - name: Install locked development dependencies")
    assert classification < install
    assert (
        'uv run --no-project python scripts/ci_changes.py --base "$CHANGE_BASE"'
        in workflow_command("Classify exact event base and head")
    )


def test_document_gate_checks_exact_diff_without_development_install(
    repo: Path,
) -> None:
    command = workflow_command("Document diff checks")
    base = git(repo, "rev-parse", "HEAD")
    head = commit_files(repo, "README.md")
    env = dict(
        os.environ, CHANGE_EVENT="pull_request", CHANGE_BASE=base, CHANGE_HEAD=head
    )
    result = subprocess.run(
        ["bash", "-e", "-c", command], cwd=repo, env=env, capture_output=True
    )
    assert result.returncode == 0
    (repo / "README.md").write_text("invalid trailing whitespace \n")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "bad document")
    env["CHANGE_HEAD"] = git(repo, "rev-parse", "HEAD")
    result = subprocess.run(
        ["bash", "-e", "-c", command], cwd=repo, env=env, capture_output=True
    )
    assert result.returncode != 0


@pytest.mark.parametrize("mode", ["100755", "160000"])
def test_executable_or_gitlink_document_requires_full_gate(
    repo: Path, mode: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    if mode == "100755":
        commit_files(repo, "README.md")
        obj = git(repo, "rev-parse", "HEAD:README.md")
    else:
        obj = base
    git(repo, "update-index", "--add", "--cacheinfo", mode, obj, "README.md")
    git(repo, "commit", "-qm", "special entry")
    assert decision(repo, base, git(repo, "rev-parse", "HEAD"))["record_only"] is False


@pytest.mark.parametrize("raw", [b"100644 blob bad\tREADME.md\0", b"incomplete", b""])
def test_invalid_or_missing_tree_evidence_cannot_exempt(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    checker = cast(
        Callable[[str, str, list[str]], bool], namespace["regular_changed_files"]
    )
    monkeypatch.setitem(checker.__globals__, "git_bytes", lambda *args: raw)
    with pytest.raises(ValueError):
        checker("a" * 40, "b" * 40, ["README.md"])

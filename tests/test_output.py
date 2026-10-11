from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from agent_harness.output import OutputStore


def test_store_rejects_outputs_symlink_without_writing_outside(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    preserved_file = outside / "preserved.txt"
    preserved_file.write_text("unchanged", encoding="utf-8")
    (state_directory / "outputs").symlink_to(outside, target_is_directory=True)
    store = OutputStore(state_directory, max_bytes=100, redactor=lambda text: text)

    with pytest.raises(ValueError):
        store.write("redacted output")

    assert list(outside.iterdir()) == [preserved_file]
    assert preserved_file.read_text(encoding="utf-8") == "unchanged"


def test_store_rejects_outputs_symlink_added_after_construction(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    store = OutputStore(state_directory, max_bytes=100, redactor=lambda text: text)
    state_directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state_directory / "outputs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        store.write("redacted output")

    assert not list(outside.iterdir())


def test_store_rejects_dangling_outputs_symlink(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    outside = tmp_path / "missing"
    (state_directory / "outputs").symlink_to(outside, target_is_directory=True)
    store = OutputStore(state_directory, max_bytes=100, redactor=lambda text: text)

    with pytest.raises(ValueError):
        store.write("redacted output")

    assert not outside.exists()
    assert list(state_directory.iterdir()) == [state_directory / "outputs"]


def test_store_replaces_artifact_symlink_without_changing_target(
    tmp_path: Path,
) -> None:
    store = OutputStore(tmp_path / "state", max_bytes=100, redactor=lambda text: text)
    original = store.write("redacted output")
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    original.path.unlink()
    original.path.symlink_to(outside)

    stored = store.write("redacted output")

    assert outside.read_text(encoding="utf-8") == "unchanged"
    assert not stored.path.is_symlink()
    assert stored.path.read_text(encoding="utf-8") == "redacted output"
    assert stored.identifier == original.identifier


def test_store_writes_only_the_redacted_output(tmp_path: Path) -> None:
    output_directory = tmp_path / "state"
    store = OutputStore(
        output_directory,
        max_bytes=100,
        redactor=lambda text: text.replace("secret-token", "[REDACTED]"),
    )

    stored = store.write("agent output: secret-token")

    assert stored.path.read_text(encoding="utf-8") == "agent output: [REDACTED]"
    assert "secret-token" not in stored.path.read_text(encoding="utf-8")
    assert stored.identifier == hashlib.sha256(stored.path.read_bytes()).hexdigest()
    assert not stored.truncated


def test_store_bounds_multibyte_output_without_invalid_utf8(tmp_path: Path) -> None:
    store = OutputStore(tmp_path / "state", max_bytes=5, redactor=lambda text: text)

    stored = store.write("가나다")

    contents = stored.path.read_text(encoding="utf-8")
    assert contents == "가"
    assert len(stored.path.read_bytes()) <= 5
    assert stored.truncated


def test_store_does_not_create_an_artifact_when_redaction_fails(tmp_path: Path) -> None:
    output_directory = tmp_path / "state"

    def fail_redaction(text: str) -> str:
        raise ValueError("redaction failed")

    store = OutputStore(output_directory, max_bytes=100, redactor=fail_redaction)

    with pytest.raises(ValueError, match="redaction failed"):
        store.write("raw secret")

    assert not output_directory.exists()


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_store_rejects_non_positive_byte_caps(max_bytes: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        OutputStore(tmp_path / "state", max_bytes=max_bytes, redactor=lambda text: text)


@pytest.mark.parametrize(
    "max_bytes", [True, False, 1.5, float("nan"), float("inf"), "10", None]
)
@pytest.mark.parametrize("existing_state", [False, True])
def test_store_rejects_malformed_caps_before_state_mutation(
    max_bytes: object, existing_state: bool, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    sentinel = state / "outputs" / "preserved.txt"
    if existing_state:
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"preserved")

    with pytest.raises(ValueError, match="positive"):
        OutputStore(state, max_bytes=cast(int, max_bytes), redactor=lambda text: text)

    if existing_state:
        assert sentinel.read_bytes() == b"preserved"
        assert list(sentinel.parent.iterdir()) == [sentinel]
    else:
        assert not state.exists()


def test_output_callback_relocation_into_git_metadata_rejected(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "repo"
    metadata = tmp_path / "metadata"
    subprocess.run(
        ["git", "init", "--quiet", "--separate-git-dir", str(metadata), str(root)],
        check=True,
        timeout=10,
    )
    state = tmp_path / "state"
    state.mkdir()
    destination = metadata / "runtime"
    destination.mkdir()

    def relocate(text: str) -> str:
        state.rmdir()
        state.symlink_to(destination, target_is_directory=True)
        return text

    store = OutputStore(state, max_bytes=128, redactor=relocate)
    with pytest.raises(ValueError):
        store.write("refused")
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("layout", ["separate", "linked-common"])
def test_outputs_directory_equal_git_metadata_is_rejected(
    tmp_path: Path, layout: str
) -> None:
    import subprocess

    state = tmp_path / "state"
    state.mkdir()
    outputs = state / "outputs"
    root = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--quiet", "--separate-git-dir", str(outputs), str(root)],
        check=True,
        timeout=10,
    )
    if layout == "linked-common":
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "fixture",
            ],
            check=True,
            timeout=10,
        )
        linked = tmp_path / "linked"
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "--quiet",
                "-b",
                "linked",
                str(linked),
            ],
            check=True,
            timeout=10,
        )
        assert (outputs / "worktrees" / "linked" / "commondir").is_file()
    before = {
        p.relative_to(outputs): p.read_bytes()
        for p in outputs.rglob("*")
        if p.is_file()
    }
    store = OutputStore(state, max_bytes=128, redactor=lambda text: text)

    with pytest.raises(ValueError, match="Git metadata"):
        store.write("refused")

    assert {
        p.relative_to(outputs): p.read_bytes()
        for p in outputs.rglob("*")
        if p.is_file()
    } == before


def test_output_callback_creating_git_metadata_at_outputs_is_rejected(
    tmp_path: Path,
) -> None:
    import subprocess

    state = tmp_path / "state"
    state.mkdir()
    outputs = state / "outputs"
    before: dict[Path, bytes] = {}

    def create_metadata(text: str) -> str:
        subprocess.run(
            [
                "git",
                "init",
                "--quiet",
                "--separate-git-dir",
                str(outputs),
                str(tmp_path / "repo"),
            ],
            check=True,
            timeout=10,
        )
        before.update(
            {
                p.relative_to(outputs): p.read_bytes()
                for p in outputs.rglob("*")
                if p.is_file()
            }
        )
        return text

    with pytest.raises(ValueError, match="Git metadata"):
        OutputStore(state, max_bytes=128, redactor=create_metadata).write("refused")

    assert before
    assert {
        p.relative_to(outputs): p.read_bytes()
        for p in outputs.rglob("*")
        if p.is_file()
    } == before

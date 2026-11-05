"""External receipt contracts; disposable commands are synthetic, never native."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_harness.policy import VerificationPolicy


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(root)], timeout=10, check=True)
    (root / "value.py").write_text("value = 1\n")
    return root


def collect(tmp_path: Path, script: str | None = None) -> tuple[Path, dict[str, Any]]:
    from agent_harness.external_checks import collect_external_check

    root = repository(tmp_path)
    bundle = tmp_path / "receipt"
    artifact = bundle / "result.xml"
    xml = '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="private-test"/></testsuite></testsuites>'
    code = script or (
        f"from pathlib import Path; Path({str(artifact)!r}).write_text({xml!r}); "
        "print('PRIVATE-OUTPUT')"
    )
    policy = VerificationPolicy(((sys.executable, "-c", code, "PRIVATE-ARG"),), ".", 2)
    result = collect_external_check(
        root,
        policy,
        bundle,
        actor="test operator",
        check_label="unit checks",
        criterion="safe collection",
        safe_command_display="python [redacted]",
    )
    return bundle, result


def test_collects_actual_safe_receipt_and_counts(tmp_path: Path) -> None:
    """Guarantee actual identities/times/counts, not raw output or argv storage."""
    from agent_harness.external_checks import read_external_check

    bundle, result = collect(tmp_path)
    assert result["process"]["state"] == "EXITED"
    assert result["tests"]["state"] == "PASS"
    assert result["tests"]["counts"] == dict(tests=1, failures=0, errors=0, skipped=0)
    loaded = read_external_check(bundle / "receipt.json")
    assert loaded["origin"] == "external_command_receipt"
    assert loaded["subject_state"] == "unchanged"
    assert loaded["started_at"] <= loaded["ended_at"]
    persisted = (bundle / "intent.json").read_text() + (
        bundle / "receipt.json"
    ).read_text()
    assert "PRIVATE-ARG" not in persisted and "PRIVATE-OUTPUT" not in persisted
    assert "private-test" not in persisted
    assert json.loads((bundle / "receipt.json").read_text())


@pytest.mark.parametrize(
    "xml,exit_code,state",
    [
        (
            '<testsuite tests="1"><testcase name="a"><failure>PRIVATE</failure></testcase></testsuite>',
            0,
            "FAIL",
        ),
        (
            '<testsuite tests="1"><testcase name="a"><skipped/></testcase></testsuite>',
            0,
            "SKIP",
        ),
        ('<testsuite tests="1"><testcase name="a"/></testsuite>', 2, "FAIL"),
        (None, 0, "UNKNOWN"),
        ('<testsuite tests="2"><testcase name="a"/></testsuite>', 0, "UNKNOWN"),
        ('<!DOCTYPE testsuite [<!ENTITY x "private">]><testsuite/>', 0, "UNKNOWN"),
        (
            '<testsuite><testcase name="a"/><testcase name="a"/></testsuite>',
            0,
            "UNKNOWN",
        ),
        (
            '<testsuite><testcase name="a"><failure/><skipped/></testcase></testsuite>',
            0,
            "UNKNOWN",
        ),
        (
            '<testsuite><testcase name="outer"><testcase name="inner"><failure/></testcase></testcase></testsuite>',
            0,
            "UNKNOWN",
        ),
    ],
)
def test_outcomes_never_promote_bad_or_missing_xml(
    tmp_path: Path, xml: str | None, exit_code: int, state: str
) -> None:
    artifact = tmp_path / "receipt" / "result.xml"
    script = (
        f"from pathlib import Path; Path({str(artifact)!r}).write_text({xml!r}); "
        if xml is not None
        else ""
    ) + f"raise SystemExit({exit_code})"
    _, result = collect(tmp_path, script)
    assert result["outcome"] == state


def test_timeout_keeps_original_process_even_if_candidate_changed(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "receipt" / "result.xml"
    script = f"from pathlib import Path; import time; Path('value.py').write_text('changed'); Path({str(artifact)!r}).write_text('<testsuite><testcase name=\"a\"/></testsuite>'); time.sleep(5)"
    _, result = collect(tmp_path, script)
    assert result["process"]["state"] == "TIMEOUT"
    assert result["tests"]["state"] == "PASS"
    assert result["outcome"] == "TIMEOUT"
    assert result["subject_state"] == "changed"


@pytest.mark.parametrize(
    "kind", ["inside", "existing", "symlink", "protected", "subdirectory"]
)
def test_rejects_unsafe_bundle_before_launch(tmp_path: Path, kind: str) -> None:
    from agent_harness.external_checks import collect_external_check

    root = repository(tmp_path)
    target = root
    marker = tmp_path / "launched"
    policy = VerificationPolicy(
        (
            (
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ),
        ),
        ".",
        2,
    )
    destination = tmp_path / "bundle"
    protected: tuple[Path, ...] = ()
    if kind == "inside":
        destination = root / "bundle"
    elif kind == "subdirectory":
        target = root / "subdirectory"
        target.mkdir()
        destination = root / "bundle"
    elif kind == "existing":
        destination.mkdir()
        (destination / "result.xml").write_text("stale")
    elif kind == "symlink":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        destination = tmp_path / "alias" / "bundle"
    else:
        protected = (tmp_path,)
    with pytest.raises((ValueError, FileExistsError)):
        collect_external_check(
            target,
            policy,
            destination,
            actor="operator",
            check_label="check",
            criterion="test",
            safe_command_display="redacted",
            protected_paths=protected,
        )
    assert not marker.exists()


@pytest.mark.parametrize(
    "mutation", ["receipt", "artifact", "missing", "hardlink", "symlink"]
)
def test_reader_refuses_tampered_or_unsafe_records(
    tmp_path: Path, mutation: str
) -> None:
    from agent_harness.external_checks import read_external_check

    bundle, _ = collect(tmp_path)
    if mutation == "receipt":
        data = json.loads((bundle / "receipt.json").read_text())
        data["payload"]["outcome"] = "FAIL"
        (bundle / "receipt.json").write_text(json.dumps(data))
    elif mutation == "artifact":
        (bundle / "result.xml").write_text("changed")
    elif mutation == "missing":
        (bundle / "intent.json").unlink()
    elif mutation == "hardlink":
        (tmp_path / "alias").hardlink_to(bundle / "result.xml")
    else:
        (bundle / "result.xml").rename(tmp_path / "raw")
        (bundle / "result.xml").symlink_to(tmp_path / "raw")
    with pytest.raises((ValueError, OSError)):
        read_external_check(bundle / "receipt.json")


def test_executable_drift_records_launch_error_without_running(tmp_path: Path) -> None:
    from agent_harness.external_checks import collect_external_check
    from agent_harness.runner import bind_verification_executables

    root = repository(tmp_path)
    checker = tmp_path / "checker"
    marker = tmp_path / "marker"
    checker.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    checker.chmod(0o700)
    policy = VerificationPolicy(((str(checker),),), ".", 2)
    selected = bind_verification_executables(policy, root)
    checker.write_text(checker.read_text() + "# drift\n")
    receipt = collect_external_check(
        root,
        policy,
        tmp_path / "bundle",
        actor="operator",
        check_label="test",
        criterion="drift",
        safe_command_display="checker",
        selections=selected,
    )
    assert receipt["process"]["state"] == "LAUNCH_ERROR"
    assert receipt["outcome"] == "UNKNOWN"
    assert not marker.exists()


@pytest.mark.parametrize("phase", ["interrupt", "terminal"])
def test_failure_preserves_intent_and_available_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import agent_harness.external_checks as external

    if phase == "interrupt":

        def interrupt(*args: Any, **kwargs: Any) -> Any:
            raise KeyboardInterrupt

        from agent_harness.runner import ControlledCommandRunner

        monkeypatch.setattr(ControlledCommandRunner, "run", interrupt)
        exception: type[BaseException] = KeyboardInterrupt
    else:
        original = external._write

        def fail_terminal(path: Path, value: object) -> None:
            if path.name == "receipt.json":
                raise OSError("terminal unavailable")
            original(path, value)

        monkeypatch.setattr(external, "_write", fail_terminal)
        exception = OSError
    with pytest.raises(exception):
        collect(tmp_path)
    bundle = tmp_path / "receipt"
    assert (bundle / "intent.json").is_file()
    assert not (bundle / "receipt.json").exists()
    if phase == "terminal":
        assert (
            json.loads((bundle / "process.json").read_text())["process"]["exit_code"]
            == 0
        )
    with pytest.raises(OSError):
        external.read_external_check(bundle / "receipt.json")


def test_protected_ignored_file_mutation_is_not_success(tmp_path: Path) -> None:
    from agent_harness.external_checks import collect_external_check

    root = repository(tmp_path)
    (root / ".gitignore").write_text("ignored\n")
    protected = root / "ignored"
    protected.write_text("before")
    bundle = tmp_path / "bundle"
    code = f"from pathlib import Path; Path('ignored').write_text('after'); Path({str(bundle / 'result.xml')!r}).write_text('<testsuite><testcase name=\"a\"/></testsuite>')"
    policy = VerificationPolicy(((sys.executable, "-c", code),), ".", 2)
    result = collect_external_check(
        root,
        policy,
        bundle,
        actor="operator",
        check_label="test",
        criterion="protected",
        safe_command_display="redacted",
        protected_paths=(protected,),
    )
    assert result["protected_state"] == "changed"
    assert result["outcome"] == "UNKNOWN"


@pytest.mark.parametrize(
    "xml",
    [
        '<testsuites name="vitest" tests="2" failures="0" errors="0" skipped="0"><testsuite name="unit" tests="2" failures="0" errors="0" skipped="0"><testcase classname="unit.test.ts" name="first"/><testcase classname="unit.test.ts" name="second"/></testsuite></testsuites>',
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="test_example" name="test_example" time="0.01"><properties><property name="safe" value="omitted"/></properties></testcase></testsuite></testsuites>',
    ],
)
def test_supported_junit_dialects_are_counts_only(tmp_path: Path, xml: str) -> None:
    artifact = tmp_path / "receipt" / "result.xml"
    _, result = collect(
        tmp_path,
        f"from pathlib import Path; Path({str(artifact)!r}).write_text({xml!r})",
    )
    assert result["tests"]["state"] == "PASS"


def test_oversized_xml_is_refused_and_intent_retained(tmp_path: Path) -> None:
    artifact = tmp_path / "receipt" / "result.xml"
    with pytest.raises(ValueError):
        collect(
            tmp_path,
            f"from pathlib import Path; Path({str(artifact)!r}).write_bytes(b'x' * (4*1024*1024+1))",
        )
    assert (tmp_path / "receipt" / "intent.json").exists()
    assert (tmp_path / "receipt" / "process.json").exists()
    assert not (tmp_path / "receipt" / "receipt.json").exists()


def test_checker_replacing_bundle_with_symlink_cannot_redirect_terminal_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "protected"
    target.mkdir()
    bundle = tmp_path / "receipt"
    archived = tmp_path / "archived"
    script = f"from pathlib import Path; b=Path({str(bundle)!r}); b.rename({str(archived)!r}); b.symlink_to({str(target)!r}, target_is_directory=True)"
    with pytest.raises(ValueError):
        collect(tmp_path, script)
    assert not (target / "process.json").exists()
    assert (archived / "intent.json").exists()

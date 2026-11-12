"""Static import contracts for the verification execution boundary."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


def _imports(source: str, module: str, *, package: bool = False) -> set[str]:
    context = module if package else module.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, context)
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)
    return imported


def _violations(source: str, module: str, *, package: bool = False) -> set[str]:
    imported = _imports(source, module, package=package)
    violations: set[str] = set()
    wrappers = {
        "agent_harness.loop",
        "agent_harness.development",
        "agent_harness.external_checks",
        "agent_harness.task_run",
    }
    pure_consumers = {
        "agent_harness.baseline",
        "agent_harness.attempt",
        "agent_harness.verification_checkpoint",
    }

    def under(target: str, name: str) -> bool:
        return target == name or target.startswith(name + ".")

    for target in imported:
        if under(target, "agent_harness.runner") and module != "agent_harness.runner":
            violations.add(target)
        if module == "agent_harness.ports.verification" and under(
            target, "agent_harness"
        ):
            if target != "agent_harness" and not under(target, "agent_harness.policy"):
                violations.add(target)
        if under(target, "agent_harness.adapters.verification"):
            if not module.startswith(
                "agent_harness.adapters.verification."
            ) and module not in {
                "agent_harness.runner",
                "agent_harness.composition.verification",
            }:
                violations.add(target)
        if under(target, "agent_harness.composition") and module not in wrappers:
            violations.add(target)
        if module in pure_consumers and (
            under(target, "agent_harness.adapters")
            or under(target, "agent_harness.composition")
        ):
            violations.add(target)
        if module == "agent_harness.adapters._executables" or module.startswith(
            "agent_harness.adapters.verification."
        ):
            if under(target, "agent_harness.adapters.agents"):
                violations.add(target)
    return violations


@pytest.mark.parametrize(
    ("module", "source"),
    [
        (
            "agent_harness.ports.verification",
            "from ..adapters.verification import local",
        ),
        ("agent_harness.ports.verification", "from ..composition import verification"),
        ("agent_harness.ports.verification", "from .. import loop"),
        ("agent_harness.baseline", "from . import runner"),
        (
            "agent_harness.attempt",
            "from .adapters.verification.local import ControlledCommandRunner",
        ),
        (
            "agent_harness.baseline",
            "from .composition.verification import create_verification_executor",
        ),
        ("agent_harness.adapters._executables", "from .verification import local"),
        ("agent_harness.adapters._executables", "from .agents import codex"),
        ("agent_harness.adapters.agents._native", "from ..verification import local"),
        ("agent_harness.adapters.verification.local", "from ..agents import codex"),
    ],
)
def test_forbidden_imports_are_reported(module: str, source: str) -> None:
    assert _violations(source, module)


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("agent_harness.baseline", "from .ports.verification import CommandResult"),
        (
            "agent_harness.composition.verification",
            "from ..adapters.verification.local import ControlledCommandRunner",
        ),
        (
            "agent_harness.runner",
            "from .adapters.verification.local import ControlledCommandRunner",
        ),
        (
            "agent_harness.loop",
            "from .composition.verification import create_verification_executor",
        ),
        (
            "agent_harness.adapters.agents._native",
            "from .._executables import _validate_executable_proofs",
        ),
        (
            "agent_harness.adapters.verification.local",
            "from .._executables import bind_verification_executables",
        ),
    ],
)
def test_allowed_imports_are_accepted(module: str, source: str) -> None:
    assert not _violations(source, module)


def test_product_verification_boundaries() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "agent_harness"
    violations = {}
    for path in root.rglob("*.py"):
        parts = path.relative_to(root.parent).with_suffix("").parts
        package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if package else parts)
        found = _violations(path.read_text(), module, package=package)
        if found:
            violations[module] = found
    assert not violations


def test_internal_loop_does_not_resolve_default_executor() -> None:
    from agent_harness import loop

    tree = ast.parse(Path(loop.__file__).read_text())
    internal = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_loop"
    )
    assert not any(
        isinstance(node, ast.Name) and node.id == "create_verification_executor"
        for node in ast.walk(internal)
    )

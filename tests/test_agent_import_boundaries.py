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
    facades = {
        "agent_harness.agent",
        "agent_harness.adapter",
        "agent_harness.claude_adapter",
    }
    providers = {
        "agent_harness.adapters.agents.codex",
        "agent_harness.adapters.agents.claude",
    }
    imported = _imports(source, module, package=package)
    violations: set[str] = set()
    for target in imported:
        legacy = any(
            target == name or target.startswith(name + ".") for name in facades
        )
        provider = next(
            (
                name
                for name in providers
                if target == name or target.startswith(name + ".")
            ),
            None,
        )
        if legacy and module not in facades:
            violations.add(target)
        if module == "agent_harness.ports" or module.startswith("agent_harness.ports."):
            if target == "agent_harness.adapters" or target.startswith(
                "agent_harness.adapters."
            ):
                violations.add(target)
        if provider:
            if module == "agent_harness.adapters.agents._native":
                violations.add(target)
            elif module in providers and module != provider:
                violations.add(target)
            elif not module.startswith(
                "agent_harness.adapters."
            ) and module not in facades | {"agent_harness.task_run"}:
                violations.add(target)
    return violations


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("agent_harness.ports.agent", "import agent_harness.adapters.agents.codex"),
        ("agent_harness.ports.agent", "from ..adapters.agents import claude"),
        ("agent_harness.ports.agent", "from agent_harness import adapter"),
        ("agent_harness.adapters.agents.codex", "from . import claude"),
        ("agent_harness.adapters.agents.claude", "from .codex import CodexCLIAdapter"),
        ("agent_harness.adapters.agents._native", "from . import codex"),
        (
            "agent_harness.adapters.agents._native",
            "from .claude import ClaudeCLIAdapter",
        ),
        ("agent_harness.loop", "from .agent import AgentAdapter"),
        ("agent_harness.attempt", "from agent_harness import adapter as backend"),
        ("agent_harness.turn", "from .adapters.agents import codex"),
        (
            "agent_harness.scope",
            "from agent_harness.adapters.agents.claude import ClaudeCLIAdapter",
        ),
    ],
)
def test_forbidden_agent_imports_are_reported(module: str, source: str) -> None:
    assert _violations(source, module)


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("agent_harness.loop", "from .ports.agent import AgentAdapter"),
        ("agent_harness.adapters.agents.codex", "from ._native import _Capture"),
        (
            "agent_harness.adapters.agents.claude",
            "from ...ports.agent import TurnBudgets",
        ),
        ("agent_harness.task_run", "from .adapters.agents import codex"),
        ("agent_harness.adapter", "from .adapters.agents.codex import CodexCLIAdapter"),
        (
            "agent_harness.claude_adapter",
            "from .adapters.agents.claude import ClaudeCLIAdapter",
        ),
        ("agent_harness.agent", "from .ports.agent import AgentAdapter"),
    ],
)
def test_allowed_agent_imports_are_accepted(module: str, source: str) -> None:
    assert not _violations(source, module)


def test_package_relative_imports_resolve_from_package_itself() -> None:
    assert "agent_harness.adapters.agents.codex" in _imports(
        "from . import codex", "agent_harness.adapters.agents", package=True
    )


def test_product_agent_import_boundaries() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "agent_harness"
    paths = sorted(root.rglob("*.py"))
    assert paths
    modules: set[str] = set()
    violations: dict[str, set[str]] = {}
    for path in paths:
        parts = path.relative_to(root.parent).with_suffix("").parts
        package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if package else parts)
        modules.add(module)
        found = _violations(path.read_text(), module, package=package)
        if found:
            violations[module] = found
    assert {
        "agent_harness.ports.agent",
        "agent_harness.adapters.agents.codex",
        "agent_harness.adapters.agents.claude",
        "agent_harness.adapters.agents._native",
    } <= modules
    assert not violations

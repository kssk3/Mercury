from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_harness.contract import TaskContract


def test_contract_is_immutable_and_accepts_canonical_scope() -> None:
    contract = TaskContract(
        goal="Add the lock",
        allowed_paths=("src/agent_harness/lock.py",),
        protected_paths=("pyproject.toml",),
        completion_criteria=("focused tests pass",),
    )

    assert contract.goal == "Add the lock"
    with pytest.raises(FrozenInstanceError):
        contract.goal = "Change scope"  # type: ignore[misc]


def test_contract_copies_mutable_inputs() -> None:
    allowed = ["src/a.py"]
    protected = ["pyproject.toml"]
    criteria = ["tests pass"]
    contract = TaskContract(
        goal="Task",
        allowed_paths=allowed,  # type: ignore[arg-type]
        protected_paths=protected,  # type: ignore[arg-type]
        completion_criteria=criteria,  # type: ignore[arg-type]
    )

    allowed.append("pyproject.toml")
    protected.clear()
    criteria.clear()

    assert contract.allowed_paths == ("src/a.py",)
    assert contract.protected_paths == ("pyproject.toml",)
    assert contract.completion_criteria == ("tests pass",)


@pytest.mark.parametrize(
    ("allowed_paths", "protected_paths"),
    [
        (("src",), ("src/private",)),
        (("src/private/a.py",), ("src/private",)),
    ],
)
def test_contract_rejects_nested_allowed_and_protected_paths(
    allowed_paths: tuple[str, ...], protected_paths: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="both"):
        TaskContract("Task", allowed_paths, protected_paths, ("tests",))


@pytest.mark.parametrize(
    ("allowed", "protected"), [("src", "src2/private"), ("src2/private", "src")]
)
def test_contract_accepts_sibling_prefixes(allowed: str, protected: str) -> None:
    contract = TaskContract("Task", (allowed,), (protected,), ("tests",))

    assert contract.allowed_paths == (allowed,)
    assert contract.protected_paths == (protected,)


@pytest.mark.parametrize(
    ("allowed_paths", "protected_paths"),
    [(("src/bad\x00name",), ()), ((), ("src/bad\x00name",))],
)
def test_contract_rejects_nul_paths(
    allowed_paths: tuple[str, ...], protected_paths: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        TaskContract("Task", allowed_paths, protected_paths, ("tests",))


@pytest.mark.parametrize(
    ("allowed", "protected"),
    [
        ("Src/A.py", "src/a.py"),
        ("Src", "src/private"),
        ("SRC/private/a.py", "src/private"),
        ("Straße/a.py", "STRASSE/a.py"),
    ],
)
def test_contract_rejects_casefold_overlap(allowed: str, protected: str) -> None:
    with pytest.raises(ValueError, match="both"):
        TaskContract("Task", (allowed,), (protected,), ("tests",))


@pytest.mark.parametrize(
    ("allowed_paths", "protected_paths"),
    [(("Src/a.py", "src/A.py"), ()), ((), ("Src/a.py", "src/A.py"))],
)
def test_contract_rejects_casefold_duplicates(
    allowed_paths: tuple[str, ...], protected_paths: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        TaskContract("Task", allowed_paths, protected_paths, ("tests",))


def test_contract_preserves_case_and_accepts_distinct_scope() -> None:
    contract = TaskContract(
        "Task", ("Src", "LIB/A.py"), ("SRC2/private", "LIB/B.py"), ("tests",)
    )

    assert contract.allowed_paths == ("Src", "LIB/A.py")
    assert contract.protected_paths == ("SRC2/private", "LIB/B.py")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"goal": ""}, "goal"),
        ({"completion_criteria": ()}, "criterion"),
        ({"allowed_paths": ("/etc/passwd",)}, "relative"),
        ({"allowed_paths": ("../escape",)}, "parent"),
        ({"allowed_paths": ("src/a.py", "src/a.py")}, "duplicate"),
        ({"allowed_paths": ("src/a.py",), "protected_paths": ("src/a.py",)}, "both"),
    ],
)
def test_contract_rejects_invalid_admission_fields(
    kwargs: dict[str, object], message: str
) -> None:
    defaults: dict[str, object] = {
        "goal": "Task",
        "allowed_paths": (),
        "protected_paths": (),
        "completion_criteria": ("tests pass",),
    }
    defaults.update(kwargs)

    with pytest.raises(ValueError, match=message):
        TaskContract(**defaults)  # type: ignore[arg-type]


def test_contract_copies_caller_owned_scope_collections() -> None:
    allowed_paths = ["src/a.py"]
    protected_paths = ["pyproject.toml"]
    completion_criteria = ["tests pass"]

    contract = TaskContract(
        "Task",
        allowed_paths,  # type: ignore[arg-type]
        protected_paths,  # type: ignore[arg-type]
        completion_criteria,  # type: ignore[arg-type]
    )
    allowed_paths.append("src/b.py")
    protected_paths.append("README.md")
    completion_criteria.append("lint passes")

    assert contract.allowed_paths == ("src/a.py",)
    assert contract.protected_paths == ("pyproject.toml",)
    assert contract.completion_criteria == ("tests pass",)

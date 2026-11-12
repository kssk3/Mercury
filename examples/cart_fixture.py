"""Fixed offline cart fixture; candidate edits are simulated, checks are real."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from agent_harness.admission import AdmissionRecord, admit
from agent_harness.composition.verification import create_verification_executor
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.agent import AgentTurnResult, CapturedText, TurnBudgets
from agent_harness.ports.verification import (
    CommandResult,
    VerificationExecutables,
    VerificationExecutor,
)

SCENARIOS = ("correct", "regression", "scope_escape", "incomplete")
PATHS = ("cart.py", "check_discount.py", "check_shipping.py", "shipping.py")
CART = "from shipping import shipping_fee\n\ndef total(subtotal, coupon):\n    merchandise = subtotal - coupon\n    return merchandise + shipping_fee(subtotal)\n"
SHIPPING = "def shipping_fee(subtotal):\n    return 0 if subtotal >= 50000 else 3000\n"
CASES = {
    "discount": ((20000, 5000, 18000), (10000, 15000, 3000)),
    "shipping": ((50000, 0, 50000), (60000, 20000, 40000), (45000, 0, 48000)),
}


def checker(command_id: str) -> str:
    return (
        "import json\nfrom cart import total\n"
        f"cases = {CASES[command_id]!r}\n"
        "rows = [dict(subtotal=s, coupon=c, expected=e, actual=total(s, c)) for s,c,e in cases]\n"
        "print(json.dumps(rows, sort_keys=True))\n"
        "raise SystemExit(0 if all(r['actual'] == r['expected'] for r in rows) else 1)\n"
    )


def build_cart_fixture(root: Path) -> None:
    for name, content in {
        "cart.py": CART,
        "shipping.py": SHIPPING,
        "check_discount.py": checker("discount"),
        "check_shipping.py": checker("shipping"),
    }.items():
        (root / name).write_text(content, encoding="utf-8")


def apply_cart_candidate(root: Path, scenario: str) -> None:
    if scenario not in SCENARIOS:
        raise ValueError("unknown cart scenario")
    content = CART.replace(
        "merchandise = subtotal - coupon", "merchandise = max(0, subtotal - coupon)"
    )
    if scenario == "regression":
        content = content.replace("shipping_fee(subtotal)", "shipping_fee(merchandise)")
    (root / "cart.py").write_text(content, encoding="utf-8")
    if scenario == "scope_escape":
        (root / "shipping.py").write_text(
            SHIPPING.replace("50000", "40000"), encoding="utf-8"
        )


def snapshots(root: Path) -> list[dict[str, str]]:
    return [
        {"path": name, "content": (root / name).read_bytes().decode("utf-8")}
        for name in PATHS
    ]


class SimulatedCartAdapter:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def prepare(self, repository_root: Path) -> None:
        pass

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> AgentTurnResult:
        admit(contract, admission)
        if sandbox != "workspace-write":
            raise ValueError("cart fixture requires workspace-write")
        root = Path(context.repository_root)
        apply_cart_candidate(root, self.scenario)
        empty = CapturedText("", 0, False)
        return AgentTurnResult(
            ("simulated-cart-edit",),
            str(root),
            "successful_exit",
            0,
            0.0,
            empty,
            empty,
            empty,
            "available",
        )


class CartExecutor:
    """Capture actual command facts and inject only the named example interruption."""

    def __init__(
        self, state: Path, scenario: str, delegate: VerificationExecutor | None = None
    ) -> None:
        self.state = state
        self.scenario = scenario
        self.delegate = delegate or create_verification_executor(max_output_bytes=8192)
        self.rows: list[dict[str, object]] = []

    def bind(
        self, policy: VerificationPolicy, repository: str | Path
    ) -> VerificationExecutables:
        return self.delegate.bind(policy, repository)

    def run(
        self,
        policy: VerificationPolicy,
        repository: str | Path,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        phase = "baseline"
        journal = self.state / "events.jsonl"
        if journal.exists():
            for line in journal.read_text().splitlines():
                event = json.loads(line)
                if event.get("event") == "verification_phase_intent":
                    phase = event["phase"]
        if self.scenario == "incomplete" and phase == "final":
            raise KeyboardInterrupt
        results = self.delegate.run(policy, repository, selections=selections)
        for result in results:
            command_id = {
                "check_discount.py": "discount",
                "check_shipping.py": "shipping",
            }[result.command[-1]]
            cases: object = None
            if not result.timed_out and result.exit_code in (0, 1):
                try:
                    cases = json.loads(result.stdout)
                except ValueError:
                    pass
            self.rows.append(
                {
                    "phase": phase,
                    "commandId": command_id,
                    "exitCode": result.exit_code,
                    "timedOut": result.timed_out,
                    "cases": cases,
                    "availability": "observed",
                }
            )
            # Raw executor facts are private and retained separately from public projection.
            with (self.state.parent / "executor.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps({"phase": phase, **asdict(result)}) + "\n")
        return results

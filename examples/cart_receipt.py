"""Explicit allowlisted public export for the owned offline cart fixture only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from cart_fixture import CART, CASES, PATHS, SCENARIOS, SHIPPING, checker

from agent_harness.inspection import summarize_execution

CAPTURE_KEYS = {
    "scenarioId",
    "sourceMode",
    "runId",
    "capturedAt",
    "mercuryRevision",
    "recordedStatus",
    "recordedReason",
    "beforeFiles",
    "afterFiles",
    "checks",
    "summary",
    "explanation",
    "contract",
}
LIMITATIONS = [
    "Historical recorded Mercury execution with deterministic simulated edits; no native model was invoked.",
    "mercuryRevision identifies the unchanged core Git base; example code may be uncommitted.",
    "Scope observation is not an OS sandbox. Technical results do not grant review or merge approval.",
    "Checker cases are captured executor facts; scope and terminal status are historical Mercury projections.",
    "No execution-configuration binding or independent product review approval is supplied by this receipt.",
    "Snapshot hashes establish content consistency, not authenticity or current-workspace verification.",
]


def digest(files: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [[f["path"], f["content"]] for f in files],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def expected_files(scenario: str | None) -> list[dict[str, str]]:
    cart = (
        CART
        if scenario is None
        else CART.replace(
            "merchandise = subtotal - coupon", "merchandise = max(0, subtotal - coupon)"
        )
    )
    if scenario == "regression":
        cart = cart.replace("shipping_fee(subtotal)", "shipping_fee(merchandise)")
    shipping = (
        SHIPPING.replace("50000", "40000") if scenario == "scope_escape" else SHIPPING
    )
    values = {
        "cart.py": cart,
        "shipping.py": shipping,
        "check_discount.py": checker("discount"),
        "check_shipping.py": checker("shipping"),
    }
    return [{"path": path, "content": values[path]} for path in PATHS]


def validate_capture(data: dict[str, Any]) -> None:
    if (
        set(data) != CAPTURE_KEYS
        or data["sourceMode"] != "recorded_simulated"
        or data["scenarioId"] not in SCENARIOS
    ):
        raise ValueError("unsupported capture")
    if data["beforeFiles"] != expected_files(None) or data[
        "afterFiles"
    ] != expected_files(data["scenarioId"]):
        raise ValueError("fixture/candidate mismatch")
    if not re.fullmatch(r"[a-f0-9]{40}", data["mercuryRevision"]) or not re.fullmatch(
        r"cart-[a-zA-Z0-9_-]+", data["runId"]
    ):
        raise ValueError("invalid provenance")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+\+00:00", data["capturedAt"]):
        raise ValueError("invalid capture time")
    for row in data["checks"]:
        if set(row) != {
            "phase",
            "commandId",
            "exitCode",
            "timedOut",
            "cases",
            "availability",
        }:
            raise ValueError("unsupported checker fields")
        if (
            row["phase"] not in ("baseline", "development", "final")
            or row["commandId"] not in CASES
            or row["availability"] != "observed"
        ):
            raise ValueError("invalid checker identity")
        if (
            row["exitCode"] is not None
            and type(row["exitCode"]) is not int
            or type(row["timedOut"]) is not bool
        ):
            raise ValueError("invalid checker result")
        if row["cases"] is not None:
            if len(row["cases"]) != len(CASES[row["commandId"]]):
                raise ValueError("invalid checker cases")
            for case, (subtotal, coupon, expected) in zip(
                row["cases"], CASES[row["commandId"]], strict=True
            ):
                if set(case) != {"subtotal", "coupon", "expected", "actual"} or any(
                    type(v) is not int for v in case.values()
                ):
                    raise ValueError("nonfixture checker payload")
                if (case["subtotal"], case["coupon"], case["expected"]) != (
                    subtotal,
                    coupon,
                    expected,
                ) or not -100000 <= case["actual"] <= 100000:
                    raise ValueError("unexpected case data")


KNOWN_DIAGNOSTICS = frozenset(
    {
        "changed_input",
        "incomplete_history",
        "input_limit",
        "invalid_record",
        "unsafe_input",
        "inconsistent_history",
        "missing_input",
        "unreadable_or_invalid_history",
        "core_history_unavailable",
        "process_unavailable",
        "scope_unavailable",
        "verification_unavailable",
        "outputs_unavailable",
    }
)


def public_diagnostics(top: object, detail: object) -> list[str]:
    values: list[str] = []
    for group in (top, detail):
        if not isinstance(group, list) or any(
            not isinstance(item, str) or item not in KNOWN_DIAGNOSTICS for item in group
        ):
            raise ValueError("unsupported diagnostic")
        values.extend(group)
    return sorted(set(values))


def export_cart_receipt(private_run: Path, output_file: Path) -> None:
    run = private_run.resolve(strict=True)
    output = output_file.resolve()
    source = Path(__file__).resolve().parents[1]
    if output == run or run in output.parents or output == source or output.is_dir():
        raise ValueError("public output overlaps private input")
    public_root = source / "web/public/demo/cart"
    if (
        source in run.parents
        or source in output.parents
        and public_root not in output.parents
    ):
        raise ValueError(
            "receipt output must be outside source or in the public cart directory"
        )
    data = json.loads((run / "capture.json").read_text())
    validate_capture(data)
    summary = summarize_execution(run / "state", details=True)
    valid = (
        summary["recorded_status"] == data["recordedStatus"]
        and summary["recorded_reason"] == data["recordedReason"]
    )
    details = summary.get("details", {})
    diagnostics = public_diagnostics(
        summary["diagnostics"],
        details.get("diagnostics", []) if isinstance(details, dict) else [],
    )
    valid = (
        valid
        and not summary["diagnostics"]
        and isinstance(details, dict)
        and not (set(details.get("diagnostics", [])) - {"verification_unavailable"})
    )
    scope = details.get("scope") if isinstance(details, dict) else None
    checks = []
    for phase in ("baseline", "development", "final"):
        for command_id in CASES:
            rows = [
                r
                for r in data["checks"]
                if r["phase"] == phase and r["commandId"] == command_id
            ]
            if len(rows) > 1:
                raise ValueError("duplicate phase result")
            if valid and rows:
                row = rows[0]
                checks.append(row)
            else:
                checks.append(
                    {
                        "phase": phase,
                        "commandId": command_id,
                        "exitCode": None,
                        "timedOut": None,
                        "cases": None,
                        "availability": "not_run" if valid else "unavailable",
                    }
                )
    # Public scope paths come only from validated historical detail, never guessed from the diff.
    scope_data: dict[str, object] = {
        "availability": "unavailable",
        "allowed": ["cart.py"],
        "protected": ["shipping.py", "check_discount.py", "check_shipping.py"],
        "changed": [],
        "violations": [],
        "proceed": None,
    }
    if valid and isinstance(scope, dict):
        delta = scope["delta"]
        changed = sorted(set(delta["added"] + delta["modified"] + delta["deleted"]))
        violations = sorted(set(scope["out_of_scope_paths"] + scope["protected_paths"]))
        if any(path not in PATHS for path in changed + violations):
            raise ValueError("nonfixture scope path")
        scope_data.update(
            availability="observed",
            changed=changed,
            violations=violations,
            proceed=scope["proceed"],
        )
    status = summary["recorded_status"]
    reason = summary["recorded_reason"]
    if (
        status not in ("pass", "fail", "blocked", "human", "unknown", "stopped")
        or reason is not None
        and not re.fullmatch(r"[a-z0-9_]+", str(reason))
    ):
        raise ValueError("unsupported outcome")
    receipt = {
        "schemaVersion": 1,
        **{
            key: data[key]
            for key in (
                "scenarioId",
                "sourceMode",
                "runId",
                "capturedAt",
                "mercuryRevision",
            )
        },
        "fixtureSha256": digest(data["beforeFiles"]),
        "candidateSha256": digest(data["afterFiles"]),
        "recordedStatus": status,
        "recordedReason": reason,
        "diagnostics": diagnostics,
        "checks": checks,
        "scope": scope_data,
        "diff": {"beforeFiles": data["beforeFiles"], "afterFiles": data["afterFiles"]},
        "limitations": LIMITATIONS,
    }
    raw = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if len(raw.encode()) > 131072:
        raise ValueError("receipt too large")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-run", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    export_cart_receipt(args.private_run, args.output_file)


if __name__ == "__main__":
    main()

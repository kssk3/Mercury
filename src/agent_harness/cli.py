import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import cast

from agent_harness import __version__
from agent_harness.contract import TaskContract
from agent_harness.profile import build_repository_profile


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(value: str) -> object:
    try:
        return json.loads(value, object_pairs_hook=_json_object)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from None
    except RecursionError:
        raise ValueError("JSON nesting is too deep") from None


def _command_json(value: str) -> list[str]:
    command = _load_json(value)
    if not isinstance(command, list):
        raise ValueError("command must be a JSON array")
    # Token validation belongs to VerificationPolicy, including NUL/type checks.
    return cast(list[str], command)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-harness")
    parser.add_argument("--version", action="store_true", dest="show_version")
    commands = parser.add_subparsers(dest="command")
    profile_command = commands.add_parser("profile", help="summarize Git filenames")
    profile_command.add_argument("path", nargs="?", default=".")
    contract_command = commands.add_parser(
        "contract", help="preview a task contract as JSON"
    )
    plan_command = commands.add_parser(
        "plan", help="preview a task and verification plan as JSON"
    )
    for preview_command in (contract_command, plan_command):
        preview_command.add_argument("--goal", required=True)
        preview_command.add_argument(
            "--allow", action="append", default=[], metavar="PATH"
        )
        preview_command.add_argument(
            "--protect", action="append", default=[], metavar="PATH"
        )
        preview_command.add_argument(
            "--criterion", action="append", required=True, metavar="TEXT"
        )
    plan_command.add_argument(
        "--command",
        action="append",
        required=True,
        dest="verification_commands",
        metavar="JSON",
    )
    plan_command.add_argument("--working-directory", default=".")
    plan_command.add_argument("--timeout-seconds", type=int, required=True)
    plan_command.add_argument("--criterion-map", required=True, metavar="JSON")
    arguments = parser.parse_args(argv)

    if arguments.show_version:
        print(f"local-harness {__version__}")
        return 0

    if arguments.command in {"contract", "plan"}:
        try:
            contract = TaskContract(
                goal=arguments.goal,
                allowed_paths=tuple(arguments.allow),
                protected_paths=tuple(arguments.protect),
                completion_criteria=tuple(arguments.criterion),
            )
            if arguments.command == "plan":
                from agent_harness.loop import bind_completion_criteria
                from agent_harness.policy import VerificationPolicy

                policy = VerificationPolicy(
                    commands=tuple(
                        tuple(_command_json(command))
                        for command in arguments.verification_commands
                    ),
                    working_directory=arguments.working_directory,
                    timeout_seconds=arguments.timeout_seconds,
                )
                bindings = bind_completion_criteria(
                    contract, policy, _load_json(arguments.criterion_map)
                )
                print(
                    json.dumps(
                        {
                            "contract": asdict(contract),
                            "verification_policy": asdict(policy),
                            "criterion_bindings": [
                                asdict(binding) for binding in bindings
                            ],
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return 0
        except ValueError as error:
            print(f"local-harness: {error}", file=sys.stderr)
            return 2
        print(contract.to_json())
        return 0

    if arguments.command == "profile":
        try:
            profile = build_repository_profile(arguments.path)
        except ValueError as error:
            print(f"local-harness: {error}", file=sys.stderr)
            return 2
        print(json.dumps(asdict(profile), sort_keys=True))
        return 0

    parser.print_help()
    return 0

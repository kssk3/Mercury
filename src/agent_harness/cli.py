import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from agent_harness import __version__
from agent_harness.contract import TaskContract
from agent_harness.profile import build_repository_profile


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-harness")
    parser.add_argument("--version", action="store_true", dest="show_version")
    commands = parser.add_subparsers(dest="command")
    profile_command = commands.add_parser("profile", help="summarize Git filenames")
    profile_command.add_argument("path", nargs="?", default=".")
    contract_command = commands.add_parser(
        "contract", help="preview a task contract as JSON"
    )
    contract_command.add_argument("--goal", required=True)
    contract_command.add_argument(
        "--allow", action="append", default=[], metavar="PATH"
    )
    contract_command.add_argument(
        "--protect", action="append", default=[], metavar="PATH"
    )
    contract_command.add_argument(
        "--criterion", action="append", required=True, metavar="TEXT"
    )
    arguments = parser.parse_args(argv)

    if arguments.show_version:
        print(f"local-harness {__version__}")
        return 0

    if arguments.command == "contract":
        try:
            contract = TaskContract(
                goal=arguments.goal,
                allowed_paths=tuple(arguments.allow),
                protected_paths=tuple(arguments.protect),
                completion_criteria=tuple(arguments.criterion),
            )
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

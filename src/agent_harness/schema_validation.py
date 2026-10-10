"""Read-only development gate for the repository's tracked project-state JSON."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _reject_constant(value: str) -> None:
    raise ValueError(f"JSON 상수가 아닙니다: {value}")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"{path}: JSON 입력을 읽을 수 없습니다: {error}") from error


def _check_local_schema(value: Any) -> None:
    """Only local fragment references and the chosen dialect are admitted."""
    if isinstance(value, dict):
        for keyword in ("$ref", "$dynamicRef"):
            if keyword in value:
                reference = value[keyword]
                if not isinstance(reference, str) or not reference.startswith("#"):
                    raise ValueError(f"{keyword}: 로컬 조각 참조만 허용됩니다")
        if "$schema" in value and value["$schema"] != DRAFT:
            raise ValueError("$schema: JSON Schema Draft 2020-12만 허용됩니다")
        for child in value.values():
            _check_local_schema(child)
    elif isinstance(value, list):
        for child in value:
            _check_local_schema(child)


def _check_reference_targets(schema: Any) -> None:
    """Check resolved inputs before the validator treats them as schemas."""
    root = Resource.from_contents(schema, default_specification=DRAFT202012)
    pending = [(root, Registry().resolver_with_root(root))]
    checked: set[int] = set()
    while pending:
        resource, resolver = pending.pop()
        if id(resource.contents) in checked:
            continue
        checked.add(id(resource.contents))
        if isinstance(resource.contents, dict):
            for keyword in ("$ref", "$dynamicRef"):
                if keyword not in resource.contents:
                    continue
                reference = resource.contents[keyword]
                diagnostic = (
                    f"{keyword} {reference!r}: 유효한 로컬 스키마 대상이 아닙니다"
                )
                try:
                    resolved = resolver.lookup(reference)
                except (Unresolvable, TypeError, ValueError) as error:
                    # Scalar traversal and invalid sequence indices are input errors.
                    raise ValueError(diagnostic) from error
                try:
                    Draft202012Validator.check_schema(resolved.contents)
                except SchemaError as error:
                    raise ValueError(diagnostic) from error
                target = Resource.from_contents(
                    resolved.contents, default_specification=DRAFT202012
                )
                pending.append((target, resolved.resolver))
        pending.extend(
            (child, resolver.in_subresource(child)) for child in resource.subresources()
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프로젝트 상태 JSON 스키마 검증")
    parser.add_argument(
        "--state", type=Path, default=Path(".project-state/current.json")
    )
    parser.add_argument(
        "--schema", type=Path, default=Path("schemas/project-state-v1.schema.json")
    )
    arguments = parser.parse_args(argv)
    try:
        schema = _read_json(arguments.schema)
        try:
            _check_local_schema(schema)
            Draft202012Validator.check_schema(schema)
            _check_reference_targets(schema)
        except (SchemaError, ValueError, RecursionError) as error:
            if isinstance(error, SchemaError):
                field = ".".join(str(item) for item in error.absolute_path) or "$"
                detail = f"{field}: 스키마 조건 {error.validator!r} 위반"
            else:
                detail = str(error)
            raise ValueError(
                f"{arguments.schema}: 유효하지 않은 스키마: {detail}"
            ) from error
        state = _read_json(arguments.state)
        # An empty registry has no retrieval callback: even URI rebasing cannot fetch.
        validator = Draft202012Validator(schema, registry=Registry())
        try:
            errors = list(validator.iter_errors(state))
        except (Unresolvable, RecursionError) as error:
            raise ValueError(
                f"{arguments.schema}: 참조를 해석할 수 없습니다: {error}"
            ) from error
        if errors:
            for validation_error in errors:
                field = (
                    ".".join(str(item) for item in validation_error.absolute_path)
                    or "$"
                )
                print(
                    f"검증 실패: {arguments.state}: {field}: "
                    f"조건 {validation_error.validator!r} 위반; "
                    f"요구값: {validation_error.validator_value!r}",
                    file=sys.stderr,
                )
            return 1
    except ValueError as error:
        print(f"검증 실패: {error}", file=sys.stderr)
        return 1
    print(f"검증 통과: {arguments.state} (스키마: {arguments.schema})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

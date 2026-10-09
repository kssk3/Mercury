# Contributing

This is a developer preview licensed under the [MIT License](LICENSE).

Keep proposed changes bounded. Describe the behavior, affected paths,
completion criteria, and verification evidence. For behavior changes, reproduce
the failure before fixing it and retain meaningful safety/recovery assertions.
Use disposable Git fixtures for tests. Preserve unrelated work and never put
credentials, private paths, native transcripts, or runtime artifacts in a patch.

Follow the [project workflow](docs/project/WORKFLOW.md) for reviewing an existing
pull request, repairing findings, authorized delivery, and branch cleanup.

## Development checks

Requirements: Python 3.12+, Git, and uv. Run from the repository root:

```sh
uv sync --locked --dev
uv run --no-sync python -m agent_harness.schema_validation --state tests/fixtures/project-state-v1.json
uv run --no-sync pytest
uv run --no-sync ruff check src tests scripts/ci_changes.py examples/quickstart.py
uv run --no-sync ruff format --check src tests scripts/ci_changes.py examples/quickstart.py
uv run --no-sync mypy src tests scripts/ci_changes.py examples/quickstart.py
uv run --no-sync python examples/quickstart.py --approve-demo
git diff --check
```

The schema validator is a development-only gate using locked development
dependencies. Its explicit input is a synthetic project-status fixture, separate
from the runtime state created by the quickstart. It checks structure and values;
it does not prove actual delivery, permission, or test execution. Runtime package
use and the example do not require the validator's development dependencies.

CI uses read-only permissions and pinned actions. Complete source, test, script,
configuration, or unknown changes require full gates; narrowly classified record
changes use proportional checks. Retain strict endpoint classification and
bootstrap checks. CI results do not establish branch protection or grant merge
authority. Record applicable local macOS verification separately from hosted
Linux results.

For a review, include a concise final diff summary, exact commands and results,
and remaining limitations. Documentation changes need link, command, and claim
checks appropriate to their scope. Reports of defects should include a minimal
reproduction and expected/observed behavior with sensitive details removed.

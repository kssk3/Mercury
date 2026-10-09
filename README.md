# Mercury

Mercury is a local reliability harness for coding agents, packaged as
`agent-harness` 0.1.0. It combines human-admitted task contracts, repository
observations, scope checks, bounded retries, and verification evidence.

```mermaid
flowchart LR
    A[Admission and caller lease] --> B[Baseline checks]
    B --> C[Agent turn]
    C --> D[Observe scope and store redacted output]
    D --> E[Development and final checks]
    E --> F[Evidence or stop]
```

This is a developer preview for callers integrating the single-agent Python
API. macOS is the initial runtime target; POSIX process and locking APIs are
required. Licensed under the [MIT License](LICENSE).

## Try the fixture example

Requirements: Python 3.12+ and Git. From the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/quickstart.py --approve-demo
```

The example creates and deletes a temporary Git repository and external state.
It uses a simulated Codex adapter, with no native agent, model, or network call.
The flag explicitly admits only the documented fixture task. See the
[quickstart](docs/quickstart.md) for its scope and expected result.

## Supported interfaces

```sh
.venv/bin/local-harness --help
.venv/bin/local-harness --version
.venv/bin/local-harness profile .
```

The CLI profiles Git filenames and emits JSON; it has no `run` subcommand.
The Python API provides the bounded single-agent loop. `CodexCLIAdapter` is
the first native adapter and requires a caller-supplied trusted Codex CLI
executable and environment.

Callers supply admission, a repository lease, scope/protected inputs, mandatory
verification commands, criterion mappings, redaction, external state, and
budgets. These are Python inputs, with no general configuration-file loader.
Scope observations are point-in-time checks, without hostile-process
confinement. The preview offers no runtime multi-agent orchestration,
messenger control, automatic native-session recovery, or power-loss durability
guarantee. Native process success alone does not establish task completion.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development checks and feedback.

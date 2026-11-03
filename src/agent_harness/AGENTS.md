# Runtime package guidance

Use [root guidance](../../AGENTS.md) for R1-R4 and
[WORKFLOW](../../docs/project/WORKFLOW.md) for lifecycle decisions. This file
provides local navigation; the full
[module map](../../docs/project/ARCHITECTURE.md#module-responsibilities) owns
the architectural description.

## Choose the relevant entry path

| Change | Start here |
| --- | --- |
| CLI arguments or settings | Trace [cli.py](cli.py) into [run_config.py](run_config.py) and [task_run.py](task_run.py). Preview and approved execution have different entry paths. |
| Common or native adapter results | Begin with [agent.py](agent.py). Read [adapter.py](adapter.py) compatibility aliases and [claude_adapter.py](claude_adapter.py) callers before changing shared native helpers. |
| Turn or phase composition | Read [turn.py](turn.py) and [loop.py](loop.py) together; use the architecture's [execution ordering](../../docs/project/ARCHITECTURE.md#execution-ordering-and-invariants) rather than reconstructing it from one caller. |
| Inventory, process execution or state decisions | Use [profile.py](profile.py) for repository inventory, [runner.py](runner.py) for checker processes, and [execution.py](execution.py) for pure execution-state transitions. These are separate responsibilities. |

## Stored contracts and readers

For an event, summary or checkpoint edit, locate its field/event name through
the [producer and consumer map](../../docs/project/ARCHITECTURE.md#persistence-and-consumer-ownership).
Include the CLI wrapper's post-release result when following a loop outcome.
Keep older/direct-loop record handling and wrapper-bound records explicit in
the affected readers; a writer change alone does not describe that interface.

[review.py](review.py) is a pure comparison API. Read its
[trusted-caller responsibilities](../../docs/review-contract.md#trusted-caller-responsibilities)
before connecting it to stored execution evidence. The proposed connection is
tracked in the [roadmap](../../docs/project/ROADMAP.md#p1-bind-execution-results-to-the-actual-review-target).

## Development-only schema surface

[schema_validation.py](schema_validation.py) validates a synthetic development
status fixture. Its optional development dependencies and fixture are separate
from normal runtime state. Use [CONTRIBUTING](../../CONTRIBUTING.md#development-checks)
for the relevant development commands; this guide adds no check gate.

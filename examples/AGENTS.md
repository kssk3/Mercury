# Example guidance

Use [root guidance](../AGENTS.md) for R1-R4 and
[WORKFLOW](../docs/project/WORKFLOW.md) for lifecycle decisions. These examples
have different task and resource contracts; choose the matching guide before
changing an example or preparing its fixture.

## Example contracts

| Script | Kind and fixed task | Detailed contract |
| --- | --- | --- |
| [quickstart.py](quickstart.py) | Offline simulated adapter changing value.txt; no native model. | [Simulator quickstart](../docs/quickstart.md#what-the-example-verifies) |
| [codex_quickstart.py](codex_quickstart.py) | Native Codex discount task in an owned disposable repository. | [Codex guide](../docs/codex-quickstart.md) |
| [python_study_example.py](python_study_example.py) | Pinned addition-CLI task with distinct source, working copy and evidence. | [Python study guide](../docs/python-study-example.md) |
| [javascript_quickstart.py](javascript_quickstart.py) | Native Claude addition task through the run CLI, checked by Node assertions. | [JavaScript guide](../docs/quickstart.md#javascript-integration-example) |

## Preparation and entry flags

Read the selected script's parser: simulator/JavaScript use `--approve-demo`,
Codex uses `--approve-codex-demo`, and Python study uses
`--approve-python-study`. A shared-looking flag is not a shared execution mode.
Python study's `--prepare-copy` selects its separate preparation path; trace
both prepare() and execute() when changing its manifest or fixture inputs.

For preview-only JavaScript onboarding, use the
[fixture recipe](../docs/run-preview.md#a-small-javascript-repository-onboarding-recipe).
The live JavaScript demo also requests execution; it is not a fixture-only
preparation command.

## Fixture and evidence coupling

Follow the selected script's BUG, CHECK or CHECKER constants into its generated
files, contract criteria and checker inputs when changing the fixed task. Keep
language-specific assertions in the selected checker rather than introducing
a second example runner. Use each guide's resource-lifecycle description.

Distinguish simulated results, fake-executable adapter contracts and recorded
native evidence when updating an example's explanation. Their actual scope is
documented in the selected guide. Public patch contents follow
[CONTRIBUTING](../CONTRIBUTING.md); native transcripts and private local records
are not example fixtures.

# Prepare the JavaScript preview fixture in one turn

**Goal.** A reader can prepare the two committed inputs for the existing preview recipe without reconstructing them from Python string literals.

**Baseline.** Mercury main at `764f0510f2e0cebec7b38f7b5bf6258414f33fa7`. The P3 proposal in [the roadmap](../../project/ROADMAP.md) motivates this bounded documentation change. [The existing example](../../../examples/javascript_quickstart.py) owns the fixture contents.

- [x] Replace the manual transcription instruction in the JavaScript onboarding section of [run-preview.md](../../run-preview.md) with a shell block that creates a fresh repository, writes the two inputs and commits them. Print its absolute path and full commit for the next step. Keep configuration preparation and preview as existing subsequent steps.
- [x] Run the documented block in a fresh shell, compare both committed blobs byte-for-byte against the example's BUG and CHECK constants, and verify exactly two tracked files, a clean tree and a matching printed HEAD.
- [x] Check the documentation diff and plan links. Finish with the local guide and a receipt of these checks. Runtime code, native model execution and publication are outside this unit.

Local verification passed with disposable fixture commit `87fcf8f278e27bdb319858a4ee43b894546038c5`. Both committed files matched the pinned example byte-for-byte. The fixture tree was clean, plan links resolved, and the guide passed `git diff --check`.

Publication review found inherited Git environment variables could redirect fixture setup. The revised shell block runs every Git command with a clean environment. It passed the same blob, tree, path and HEAD checks in a normal shell and with conflicting Git repository, work-tree and index variables.

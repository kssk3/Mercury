# Agent guidance

Read [docs/project/WORKFLOW.md](docs/project/WORKFLOW.md) before changing or
reviewing this repository. It is the project lifecycle authority.
Use [CONTRIBUTING.md](CONTRIBUTING.md) for development checks.
Keep work within the requested scope and preserve unrelated changes.

## Code Review Rules

Apply each rule only to changes within its stated scope, including directly
affected callers. The linked WORKFLOW owns
lifecycle procedures; these rules identify product defects, not new process gates.

### R1. Preserve trusted inputs through actual execution

For executable selection, Git queries, checkers, and preparation evidence, flag
changes that execute a different program than the trusted selector returned,
allow caller-controlled startup settings to alter verification, or silently
replace an admitted baseline with the current mutable state. Preserve applicable
isolation and provenance checks through invocation and retry. Use the selected
executable and verified preparation inputs; reject mismatches or require explicit
fresh preparation under the existing contract. Do not require one platform's
hard-coded executable path or treat historical evidence as a new baseline.

### R2. Preserve source and protected-path boundaries at every entry point

For copy preparation, execution APIs/CLIs, output paths, and scope observation,
flag paths that can write into the original source or protected locations before
the applicable boundary check, including resolved aliases and relocated evidence
paths. Preparation-only checks must not leave execution entry points unguarded.
Keep the admitted source/copy/evidence binding and observe the protected entries
required by the contract, including ignored files or Git metadata where covered.
Allow admitted working-copy changes, but do not report protected mutations as
successful. Point-in-time path and snapshot checks are not an OS sandbox or a
guarantee against concurrent path changes.

### R3. Persist recovery evidence and report only observed outcomes

For adapter preparation, turns, journals, restart recovery, and result summaries,
flag repository-mutating preparation before the required saved intent/before
snapshot, or failure/cancellation paths that lose the existing recovery evidence.
Preserve incomplete intent when terminal recording fails; do not invent a native
launch, output, scope clearance, or verification success. Validate result values
against the serialization contract before persistence, including plain-string
status requirements. Keep preparation ownership with the actual turn; validation
and verification-only continuation must not invoke preparation again. Readers and
summaries must preserve unknown or incomplete outcomes rather than promote them
to PASS. This requires existing recovery semantics, not automatic rollback or
power-loss durability beyond the supported contract.

### R4. Require a concrete purpose for added complexity

For new abstractions, configuration options, dependencies, and duplicated logic,
flag additions that expand beyond the admitted behavior or add concrete
maintenance cost without serving an approved use case, compatibility requirement,
or safety contract. Identify the affected code, the unsupported purpose or cost,
and a smaller alternative that preserves the required behavior. Prefer existing
facilities where they satisfy the same contract; an approved extension boundary
can be justified even with one current implementation. Do not flag code merely
for its length, style, or presumed AI authorship. Preserve required security
checks, recovery handling, and validation at distinct trust boundaries; propose
consolidation only when those protections remain intact.

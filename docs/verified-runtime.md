# Verified runtime and bounded diagnostics

The explicit `scripts/verified_runtime.py` launcher verifies a frozen source
inventory before importing Mercury. It uses only Python's standard library.
The regular development CLI remains available. Runtime integrity does not approve
a configuration, establish native executable readiness or authorize a model run.
Follow the existing [project workflow](project/WORKFLOW.md) for delivery authority.

## Prepare an inactive runtime

Preparation belongs to a trusted human/operator. Choose a reviewed revision and
content, an absolute trusted Python 3.12+ executable, an empty destination outside
model-writable frontend/output areas, and a separate receipt directory. The preserved development checkout keeps its guidance and other files; the
exported runtime bundle is a separate copy of Python files and frozen package metadata. Export
only the reviewed `src/` Python files and the reviewed distribution's
`agent_harness-VERSION.dist-info/METADATA` as ordinary copies, with no symlinks,
hardlinks, bytecode or native extensions. Copy the reviewed bootstrap to a trusted
location outside that model-writable area. Do not export a dirty checkout by
calling it a reviewed revision: record the reviewed diff/content identity too.

Run the following recipe using the selected trusted Python with `-I -S -B`.
Replace the absolute paths and revision with the admitted values first.
This is preparation, never a launch-time baseline regeneration step.

```python
import hashlib
import json
from pathlib import Path

runtime = Path("/absolute/inactive/runtime").resolve(strict=True)
bootstrap = Path("/absolute/trusted/verified_runtime.py").resolve(strict=True)
python = Path("/absolute/trusted/python3.12").resolve(strict=True)
receipt = Path("/absolute/trusted/receipts/runtime-manifest.json")
revision = "REPLACE_WITH_REVIEWED_40_CHARACTER_GIT_SHA"
metadata_source = Path("/absolute/trusted/development/site-packages/agent_harness-0.1.0.dist-info/METADATA")
# Copy the reviewed package METADATA before freezing; never add site-packages.
# Its standard Name/Version fields support __init__ under -S.
import shutil
metadata_target = runtime / "src" / metadata_source.parent.name / "METADATA"
metadata_target.parent.mkdir(exist_ok=False)
shutil.copyfile(metadata_source, metadata_target)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

files = {}
for path in sorted((runtime / "src").rglob("*")):
    if path.is_symlink():
        raise ValueError("export contains a symlink")
    if path.is_dir():
        continue
    metadata = (path.name == "METADATA" and
                path.parent.name.startswith("agent_harness-") and
                path.parent.name.endswith(".dist-info") and
                path.parent.parent == runtime / "src")
    if not path.is_file() or not (path.suffix == ".py" or metadata) or path.stat().st_nlink != 1:
        raise ValueError("export contains an unsupported entry")
    files[path.relative_to(runtime / "src").as_posix()] = digest(path)
manifest = {
    "schema_version": 1,
    "source_revision": revision,
    "runtime_root": str(runtime),
    "python": {"path": str(python), "sha256": digest(python)},
    "bootstrap": {"path": str(bootstrap), "sha256": digest(bootstrap)},
    "files": files,
}
# Exclusive creation prevents silently replacing an existing admitted baseline.
with receipt.open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
print(digest(receipt))
```

Keep the manifest digest and the Python/bootstrap identities in the operator's
preparation receipt, alongside the reviewed source/diff identity. Pin that exact
digest in a trusted launcher invocation. Recheck Python/bootstrap hashes against
the receipt before use. Do not resolve an interpreter from the model's `PATH`.

```sh
/absolute/trusted/python3.12 -I -S -B /absolute/trusted/verified_runtime.py \
  --root /absolute/inactive/runtime \
  --manifest /absolute/trusted/receipts/runtime-manifest.json \
  --manifest-sha256 REPLACE_WITH_PINNED_SHA256 -- --version
```

After `--`, pass the normal CLI arguments, such as `run --preview --config ...`
or `status --state-dir ...`. Preview and historical status retain their existing
semantics; the guard does not add native readiness checks. A real approved run
still requires the existing configuration digest/approver and native admission.
Preparation and this document never select an active frontend runtime or replay
an existing configuration.

## Frozen inventory and failure behavior

The manifest has exactly the fields shown above. JSON duplicate keys, malformed
revisions/digests, extra fields, unsupported versions and unsafe relative paths
are rejected. The canonical runtime root, Python and bootstrap paths must match.
All files under `src/` must match the complete frozen inventory; every admitted
file must be `.py`, except the fixed standard distribution
`agent_harness-VERSION.dist-info/METADATA` path required by the package version
lookup. At most one such metadata file is admitted; Python files inside
dist-info directories are refused. The metadata is copied from the trusted development preparation and
frozen too; `.pth`, editable hooks and other site-packages files are excluded. Extra files, including stale bytecode and extension shadows,
missing/changed files, links and special filesystem entries fail closed. Empty
ordinary directories do not affect imports. Canonical paths must have no symlink
components. Bounds are 2 MiB manifest bytes, 10,000 files, 20,000 traversed entries,
16 MiB per source file and 256 MiB aggregate source bytes.

Failure exits 78 with the fixed message `verified runtime integrity check failed`
before importing `agent_harness`, querying Git, preparing adapters or creating
state. No fallback source, installed package or changed manifest is accepted.
`-I -S -B` is required: isolated startup ignores `PYTHONPATH`/`PYTHONHOME`, skips
site initialization and prevents source-tree bytecode writes. There are no
third-party runtime dependencies. Development schema validation still needs its
separate locked development environment and is not a runtime CLI command.

The interpreter, bootstrap and invocation/trust anchor are trusted before they
start. An attacker who can replace those inputs can replace the checks too.
Hashes are content identity, not signatures or full interpreter/stdlib attestation.
The scan is point-in-time: it does not prevent concurrent changes between checking
and importing, and it is not an OS sandbox. Runtime source identity, configuration
approval and native executable trust are separate boundaries.

## Diagnostic observations

Codex adapter results may carry optional diagnostic observations. Legacy/custom
adapters may omit them. Validated diagnostics are stored on the existing
`turn_observation` or applicable returned-result `turn_observation_failed` journal
event with its attempt ID. No new current-state schema or raw transcript archive
is added. An exception/cancellation with no returned result invents no diagnostics.
Failed terminal recording preserves the existing saved intent for recovery.

Milestones retain observed process/stdin/stdout/stderr/exit/timeout/cleanup times;
missing facts remain null. The monotonic timer still controls timeout. A signed
wall-clock discrepancy is diagnostic only. Native JSON lines are independently
bounded to 16,384 bytes, with 32 retained allowlisted events and truncation/drop
counters. Command/message/reasoning/environment/secret payloads are excluded.
Only fixed lexical error hints, allowed event/status kinds, applicable integer
command exits and a bounded singleton `MERCURY_DIAGNOSTIC_TOOL_LIMITATION=`
self-report are retained. A self-report never proves that a tool executed or a
verification passed. Process outcomes, scope decisions, retry rules and final
verification retain their existing authority; diagnostics cannot promote a
historical/incomplete result to PASS.

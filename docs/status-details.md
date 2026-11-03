# Historical status details

Run `local-harness status --state-dir PATH --details` to add process, scope,
verification, usage and output metadata to the normal historical JSON projection.
Removing the `details` member leaves the default projection unchanged.

Details and `--explain` are mutually exclusive. HTML output, configuration and
comparison options require `--explain`. Details do not create an HTML schema.

The reader consumes one validated current/journal snapshot. It does not run Git,
checkers or agents, acquire runtime locks, read output bodies or verify the live
workspace. Missing or contradictory detail evidence is unavailable with a fixed
diagnostic; it never upgrades the historical outcome or grants approval.

Both full schema 1 and compact schema 2 histories remain supported. Compact
records retain their bounded current-state representation; verification details
come from the validated journal rather than reconstructing full inventories.
Wrapper failure outcomes and recorded review bindings retain their core meaning.

Usage reports saved loop command boundaries and requested turn intents. Native
launch count and output retention/content availability remain unknown. Output
identifiers and byte metadata do not prove that payloads still exist.

Repository profiling bounds each Git query to 10 seconds, 8 MiB of stdout and
64 KiB of stderr, with at most one additional cleanup second. Cancellation is
propagated after bounded cleanup. Cleanup signals only an owned unreaped process
group; completed leaders are not signaled. These process bounds are separate
from runtime task budgets and do not form an operating-system sandbox.

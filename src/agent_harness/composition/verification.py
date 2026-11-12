"""Compose the default trusted local verification executor without I/O."""

from agent_harness.adapters.verification.local import ControlledCommandRunner
from agent_harness.ports.verification import VerificationExecutor


def create_verification_executor(
    *, max_output_bytes: int = 1048576
) -> VerificationExecutor:
    return ControlledCommandRunner(max_output_bytes=max_output_bytes)

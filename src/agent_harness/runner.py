"""Compatibility aliases for verification contracts and local execution."""

from agent_harness.adapters._executables import (
    bind_verification_executables as bind_verification_executables,
)
from agent_harness.adapters.verification.local import (
    ControlledCommandRunner as ControlledCommandRunner,
)
from agent_harness.ports.verification import CommandResult as CommandResult
from agent_harness.ports.verification import (
    VerificationExecutable as VerificationExecutable,
)
from agent_harness.ports.verification import (
    VerificationExecutables as VerificationExecutables,
)

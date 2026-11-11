"""Compatibility imports for the canonical Codex adapter and agent types."""

from agent_harness.adapters.agents.codex import CodexCLIAdapter as CodexCLIAdapter
from agent_harness.ports.agent import AgentTurnResult as AgentTurnResult
from agent_harness.ports.agent import CapturedText as CapturedText
from agent_harness.ports.agent import TurnBudgets as TurnBudgets

CodexTurnResult = AgentTurnResult

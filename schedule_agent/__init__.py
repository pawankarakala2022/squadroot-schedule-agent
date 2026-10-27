"""SquadRoot Schedule Agent core library (no I/O, no web framework)."""

from .agent import ScheduleAgent
from .errors import AgentError, ModelError
from .explain import explain_proposal
from .llm import ChatFn, ModelConfig, chat_fn
from .models import (
    ApplyResult,
    Busy,
    CheckResult,
    Field,
    Plan,
    Problem,
    ProposedEvent,
    Proposal,
    Requirement,
    Rules,
    Shortfall,
    Team,
)
from .understand import understand_request

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "AgentError",
    "ModelError",
    "Team",
    "Field",
    "Busy",
    "Problem",
    "Rules",
    "Requirement",
    "ProposedEvent",
    "CheckResult",
    "Shortfall",
    "Proposal",
    "Plan",
    "ApplyResult",
    "ScheduleAgent",
    "ModelConfig",
    "ChatFn",
    "chat_fn",
    "understand_request",
    "explain_proposal",
]

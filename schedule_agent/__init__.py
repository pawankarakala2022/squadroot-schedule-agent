"""SquadRoot Schedule Agent core library (no I/O, no web framework)."""

from .errors import AgentError, ModelError
from .models import (
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

__version__ = "0.1.0"

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
]

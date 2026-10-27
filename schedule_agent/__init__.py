"""SquadRoot Schedule Agent core library (no I/O, no web framework)."""

from .agent import ScheduleAgent
from .cancel import CancelFilter, parse_cancel_filter
from .disruption import Disruption, parse_disruption
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
from .notify import FamilyNotice, FamilyNotices, draft_notices
from .understand import understand_request
from .vision import GameImport, ScheduleImport, extract_games, games_to_busy
from .voice import VoiceConfig, transcribe_audio

__version__ = "4.0.0"

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
    "Disruption",
    "parse_disruption",
    "CancelFilter",
    "parse_cancel_filter",
    "ScheduleAgent",
    "ModelConfig",
    "ChatFn",
    "chat_fn",
    "understand_request",
    "explain_proposal",
    "FamilyNotice",
    "FamilyNotices",
    "draft_notices",
    "VoiceConfig",
    "transcribe_audio",
    "GameImport",
    "ScheduleImport",
    "extract_games",
    "games_to_busy",
]

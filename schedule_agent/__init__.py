"""SquadRoot Schedule Agent core library (no I/O, no web framework)."""

from .agent import ScheduleAgent
from .availability import AvailabilityParse, parse_availability
from .cancel import CancelFilter, parse_cancel_filter
from .dates import problem_date_hints, relative_date_hints, today_in_tz
from .disruption import Disruption, parse_disruption
from .errors import AgentError, ModelError
from .explain import explain_proposal
from .holidays import holiday_dates, us_federal_holidays, with_us_holidays
from .llm import ChatFn, ModelConfig, chat_fn
from .router import Route, parse_route_intent
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
from .vision import (
    AvailabilityImport,
    CoachUnavailability,
    GameImport,
    GroundAvailability,
    ScheduleImport,
    availability_to_busy,
    extract_availability,
    extract_games,
    games_to_busy,
    games_to_events,
)
from .voice import VoiceConfig, transcribe_audio

__version__ = "5.1.0"

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
    "Route",
    "parse_route_intent",
    "today_in_tz",
    "relative_date_hints",
    "problem_date_hints",
    "us_federal_holidays",
    "holiday_dates",
    "with_us_holidays",
    "AvailabilityParse",
    "parse_availability",
    "GroundAvailability",
    "CoachUnavailability",
    "AvailabilityImport",
    "extract_availability",
    "availability_to_busy",
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
    "games_to_events",
]

"""Public errors. AgentError is safe to show a manager; ModelError means retry later."""


class AgentError(Exception):
    """Bad request or could not understand. Safe to show the manager."""


class ModelError(Exception):
    """Provider failure. Safe to show as 'assistant unavailable'."""

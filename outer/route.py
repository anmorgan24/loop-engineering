"""Routing logic for the outer loop.

The outer loop can only act on the exit reason the inner loop reports, so the
exit reason is the entire interface between the two loops. route() maps every
member of ExitReason to a downstream action. It raises on any unknown value so
that a new ExitReason member cannot silently fall through.

Import rule: outer/ may import agent/. outer/ must never import evals/.
"""

from __future__ import annotations

from enum import Enum

from agent.gate import ExitReason


class Route(Enum):
    ACCEPT            = "accept"
    HUMAN_PERMISSIONS = "human_permissions"
    RETRY             = "retry"
    HUMAN_REVIEW      = "human_review"


_RETRY_EXITS = frozenset({ExitReason.NO_PROGRESS, ExitReason.BUDGET_EXHAUSTED})


def route(exit_reason: str, attempt: int) -> Route:
    """Map an exit reason to the action the outer loop should take.

    attempt is 1-based. RETRY is only returned on attempt 1; subsequent
    attempts always resolve to a terminal route.

    Raises ValueError on any exit_reason string not in ExitReason, so new
    enum members cannot be silently ignored.
    """
    er = ExitReason(exit_reason)   # raises ValueError if unknown

    if er in (ExitReason.VERIFIED_SUCCESS, ExitReason.MODEL_SAID_DONE):
        return Route.ACCEPT
    if er is ExitReason.HARD_BLOCKER:
        return Route.HUMAN_PERMISSIONS
    if er in _RETRY_EXITS:
        return Route.RETRY if attempt == 1 else Route.HUMAN_REVIEW
    if er is ExitReason.ESCALATE:
        return Route.HUMAN_REVIEW

    # Unreachable: ExitReason(exit_reason) above exhausts all members.
    raise AssertionError(f"unhandled ExitReason member: {er!r}")

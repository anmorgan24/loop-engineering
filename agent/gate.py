"""State, budget, and the gate.

This is the file the talk is about. Everything in it is deterministic and unit
testable, which is the argument: the loop is a state machine, and only one box
inside it is non-deterministic.

`should_continue` is the only thing that can end the loop. Nothing else in
loop.py is allowed to return. That constraint is what makes the exit reason a
reliable field, and the exit reason is the field the whole measurement half of
the talk hangs on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from agent.classify import MAX_MALFORMED_REPAIRS, Classification, ErrorClass


class ExitReason(str, Enum):
    """The five exit classes.

    Most shipped loops have two: MODEL_SAID_DONE and BUDGET_EXHAUSTED, usually
    spelled `max_iterations`. Premature exit and runaway then both surface to
    the user as "the agent is unreliable", because there is no field that tells
    them apart.
    """
    VERIFIED_SUCCESS = "verified_success"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    HARD_BLOCKER = "hard_blocker"
    ESCALATE = "escalate"

    # Present only in the naive loop, for contrast. An exit the model controls
    # is an exit that absorbs all the pressure.
    MODEL_SAID_DONE = "model_said_done"


@dataclass
class Budget:
    """Budget is not one number.

    Capping iterations alone lets a single runaway query burn the wall clock,
    and capping tokens alone lets a cheap loop spin forever. Each dimension
    catches a failure the others miss.
    """
    max_iterations: int = 12
    max_tokens: int = 60_000
    max_seconds: float = 120.0
    max_usd: float = 0.50

    iterations: int = 0
    tokens: int = 0
    usd: float = 0.0
    started_at: float = field(default_factory=time.monotonic)

    def charge(self, tokens: int = 0, usd: float = 0.0, iteration: bool = True) -> None:
        self.tokens += tokens
        self.usd += usd
        if iteration:
            self.iterations += 1

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def exhausted(self, multi: bool = True) -> Optional[str]:
        if self.iterations >= self.max_iterations:
            return f"iterations {self.iterations}/{self.max_iterations}"
        if not multi:
            return None
        if self.tokens >= self.max_tokens:
            return f"tokens {self.tokens}/{self.max_tokens}"
        if self.elapsed >= self.max_seconds:
            return f"wall clock {self.elapsed:.0f}s/{self.max_seconds:.0f}s"
        if self.usd >= self.max_usd:
            return f"cost ${self.usd:.3f}/${self.max_usd:.2f}"
        return None


@dataclass
class Attempt:
    iteration: int
    action: str
    sql: Optional[str]
    sql_hash: Optional[str]
    rung_reached: int
    ok: bool
    error_class: ErrorClass
    detail: str


@dataclass
class State:
    question: str
    attempts: list[Attempt] = field(default_factory=list)
    answer_sql: Optional[str] = None
    answer_rows: list = field(default_factory=list)
    answer_columns: list = field(default_factory=list)
    blocked: Optional[Classification] = None
    malformed_streak: int = 0
    transcript: list = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.answer_sql is not None

    @property
    def max_rung(self) -> int:
        return max((a.rung_reached for a in self.attempts), default=0)

    def record(self, attempt: Attempt) -> None:
        self.attempts.append(attempt)
        if attempt.error_class is ErrorClass.MALFORMED:
            self.malformed_streak += 1
        else:
            self.malformed_streak = 0


@dataclass
class ExitDecision:
    stop: bool
    reason: Optional[ExitReason] = None
    detail: str = ""


# How many consecutive query attempts may fail to advance before the loop
# concedes. Kept low: three identical fingerprints is not bad luck.
NO_PROGRESS_WINDOW = 3


def repeated_action(state: State, window: int = NO_PROGRESS_WINDOW) -> Optional[str]:
    """Has the agent submitted the same query, modulo formatting, `window` times?

    Canonicalising before hashing is what makes this work. An agent that
    reformats a query and narrates a revision in its reasoning has still taken
    the same action, and the fingerprint says so.
    """
    hashes = [a.sql_hash for a in state.attempts if a.sql_hash]
    if len(hashes) < window:
        return None
    tail = hashes[-window:]
    return tail[0] if len(set(tail)) == 1 else None


def rung_stalled(state: State, window: int = NO_PROGRESS_WINDOW) -> bool:
    """Is the agent failing at the same rung, repeatedly, without advancing?

    Three iterations stuck at rung 2 means the agent does not understand the
    schema. Three stuck at rung 5 means it does, and is wrong about the
    semantics. Different problems, and neither is fixed by a fourth attempt.
    """
    query_attempts = [a for a in state.attempts if a.sql is not None]
    if len(query_attempts) < window:
        return False
    tail = query_attempts[-window:]
    return len({a.rung_reached for a in tail}) == 1 and not any(a.ok for a in tail)


def should_continue(state: State, budget: Budget, config=None) -> ExitDecision:
    """The only function permitted to end the loop.

    Order matters. Success is checked first so a solved task never reports as
    budget exhausted. Hard blockers are checked before budget so the exit
    reason names the real cause rather than the symptom that showed up last.
    """
    if state.verified:
        return ExitDecision(True, ExitReason.VERIFIED_SUCCESS,
                            f"verified at iteration {budget.iterations}")

    if state.blocked is not None:
        return ExitDecision(True, ExitReason.HARD_BLOCKER, state.blocked.detail)

    if state.malformed_streak >= MAX_MALFORMED_REPAIRS:
        return ExitDecision(True, ExitReason.ESCALATE,
                            f"{state.malformed_streak} malformed actions in a row")

    if config is None or config.detect_no_progress:
        dup = repeated_action(state)
        if dup is not None:
            return ExitDecision(True, ExitReason.NO_PROGRESS,
                                f"same query fingerprint {NO_PROGRESS_WINDOW} times running")

        if rung_stalled(state):
            return ExitDecision(True, ExitReason.NO_PROGRESS,
                                f"stuck at rung {state.max_rung} for "
                                f"{NO_PROGRESS_WINDOW} attempts")

    spent = budget.exhausted(multi=(config is None or config.multi_budget))
    if spent is not None:
        return ExitDecision(True, ExitReason.BUDGET_EXHAUSTED, spent)

    return ExitDecision(False)

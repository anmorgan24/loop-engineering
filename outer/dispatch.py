"""Dispatcher: runs the inner loop over a list of tasks, retrying once where
the route says to.

Import rule: outer/ may import agent/. outer/ must never import evals/.
Callers in evals/ are allowed to import outer/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent import loop
from agent.config import ENGINEERED, LoopConfig
from agent.db import connect
from agent.gate import Budget
from agent.verify import Invariants
from outer.route import Route, route


@dataclass
class Task:
    """Minimal description of one question for the dispatcher.

    Callers build these from evals/questions.py or any other source. The
    dispatcher does not import evals/, so the question catalogue is not a
    hard dependency of the control plane.
    """
    id: str
    text: str
    invariants: Optional[Invariants] = None
    budget_kwargs: dict = field(default_factory=dict)


@dataclass
class DispatchRecord:
    """One record per task. outcomes has length 1 unless a retry occurred."""
    question_id: str
    outcomes: list          # list[loop.Outcome]
    final_route: Route


def dispatch(
    tasks,
    conn=None,
    config: Optional[LoopConfig] = None,
) -> list[DispatchRecord]:
    """Run the inner loop over each task, retrying once where route says so.

    The llm.propose_action seam is the test hook: swap it before calling
    dispatch to drive the loop with a scripted model.

    A task that routes to RETRY on attempt 1 gets one more run. If that
    second run also resolves to RETRY it instead routes to HUMAN_REVIEW,
    because route() returns RETRY only on attempt 1.
    """
    conn = conn or connect()
    config = config or ENGINEERED
    records: list[DispatchRecord] = []

    for task in tasks:
        attempt_num = 1
        outcomes = []
        while True:
            budget_kwargs = task.budget_kwargs or {}
            outcome = loop.run(
                question=task.text,
                question_id=task.id,
                invariants=task.invariants,
                budget=Budget(**budget_kwargs) if budget_kwargs else None,
                conn=conn,
                config=config,
            )
            outcomes.append(outcome)
            r = route(outcome.exit_reason, attempt_num)
            if r is not Route.RETRY:
                break
            attempt_num += 1

        records.append(DispatchRecord(
            question_id=task.id,
            outcomes=outcomes,
            final_route=r,
        ))

    return records

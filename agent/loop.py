"""The engineered loop.

Read `run` first. It is the slide. Everything it calls is deterministic except
`propose_action`, and everything that can end it is inside `should_continue`.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Optional

from agent import llm
from agent.classify import (Classification, ErrorClass, classify_exception,
                            classify_tool_error, classify_verifier, naive_feedback)
from agent.config import ENGINEERED, LoopConfig
from agent.db import connect, schema_map
from agent.gate import (Attempt, Budget, ExitDecision, ExitReason, State,
                        should_continue)
from agent.llm import track, update_span, update_trace
from agent.tools import TOOL_NAMES, TOOL_SPECS, dispatch
from agent.verify import Invariants, Rung, canonical_hash, verify


@dataclass
class Outcome:
    question_id: str
    question: str
    exit_reason: str
    iterations: int
    tokens: int
    usd: float
    seconds: float
    answer_sql: Optional[str]
    answer_rows: list
    max_rung: int
    attempts: list
    detail: str = ""

    def summary(self) -> dict:
        return {
            "question_id": self.question_id,
            "exit_reason": self.exit_reason,
            "iterations": self.iterations,
            "tokens": self.tokens,
            "usd": round(self.usd, 5),
            "seconds": round(self.seconds, 2),
            "max_rung": self.max_rung,
            "solved_at_runtime": self.exit_reason == ExitReason.VERIFIED_SUCCESS.value,
        }


@track(name="verify", type="tool")
def _verify_step(sql, conn, schema, inv, config):
    stop_after = Rung.INVARIANTS if config.verify_invariants else Rung.EXECUTE
    result = verify(sql, conn, schema, inv, stop_after=stop_after)
    update_span(metadata={"rung": int(result.rung), "ok": result.ok,
                          "empty_cause": result.empty_cause})
    return result


@track(name="tool", type="tool")
def _tool_step(name, args, conn):
    update_span(metadata={"tool": name})
    return dispatch(name, args, conn)


@track(name="model", type="llm")
def _model_step(messages, tools):
    turn = llm.propose_action(messages, tools)
    update_span(metadata={"input_tokens": turn.input_tokens,
                          "output_tokens": turn.output_tokens,
                          "stop_reason": turn.stop_reason})
    return turn


@track(name="sql_agent_run")
def run(question: str, question_id: str = "", invariants: Optional[Invariants] = None,
        budget: Optional[Budget] = None, conn=None, verbose: bool = False,
        config: Optional[LoopConfig] = None) -> Outcome:
    config = config or ENGINEERED
    conn = conn or connect()
    schema = schema_map(conn)
    budget = budget or Budget()
    inv = invariants or Invariants()
    state = State(question=question)
    started = time.monotonic()

    messages = [{"role": "user", "content": question}]

    while True:
        # The only thing that can end this loop.
        decision: ExitDecision = should_continue(state, budget, config)
        if decision.stop:
            return _finish(state, budget, decision, question, question_id, started, config)

        # The one non-deterministic step.
        try:
            turn = _model_step(messages, TOOL_SPECS)
        except BaseException as e:  # noqa: BLE001
            c = classify_exception(e)
            if c.kind is ErrorClass.TRANSIENT:
                time.sleep(c.retry_after_s)
                continue  # no iteration charged, no progress charged
            if c.kind is ErrorClass.HARD_BLOCKER:
                state.blocked = c
                continue
            raise

        budget.charge(tokens=turn.tokens, usd=turn.usd, iteration=True)
        messages.append({"role": "assistant", "content": turn.raw_content})

        if not turn.tool_calls:
            # No action proposed. That is a malformed turn, not a completion:
            # the model does not get to end this loop by saying it is finished.
            state.record(Attempt(budget.iterations, "none", None, None, 0, False,
                                 ErrorClass.MALFORMED, "no tool call"))
            messages.append({"role": "user", "content":
                             "You did not call a tool. Call one, or submit_answer."})
            continue

        results = []
        for call in turn.tool_calls:
            feedback, attempt = _handle_call(call, state, budget, conn, schema, inv,
                                             verbose, config)
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": feedback})
            if attempt is not None:
                state.record(attempt)
        messages.append({"role": "user", "content": results})


def _handle_call(call, state, budget, conn, schema, inv, verbose, config):
    name, args = call["name"], call.get("input") or {}

    if name not in TOOL_NAMES:
        c = classify_tool_error(name, TOOL_NAMES, f"unknown tool {name}")
        return c.feedback, Attempt(budget.iterations, name, None, None, 0, False,
                                   c.kind, c.detail)

    if name == "submit_answer":
        sql = args.get("sql", "")
        result = _verify_step(sql, conn, schema, inv, config)
        c = classify_verifier(result, detect_blockers=config.detect_blockers)

        if c.kind is ErrorClass.HARD_BLOCKER:
            state.blocked = c
        if result.ok:
            state.answer_sql = sql
            state.answer_rows = result.rows
            state.answer_columns = result.columns

        if verbose:
            mark = "PASS" if result.ok else "FAIL"
            print(f"  [{budget.iterations}] submit -> rung {int(result.rung)} {mark} "
                  f"{c.kind.value if not result.ok else ''}")

        feedback = ("Accepted." if result.ok
                    else (c.feedback if config.classify_errors else naive_feedback(result)))
        return feedback, Attempt(budget.iterations, name, sql, canonical_hash(sql),
                                 int(result.rung), result.ok, c.kind, result.detail)

    try:
        out = _tool_step(name, args, conn)
        if verbose:
            print(f"  [{budget.iterations}] {name}")
        return out, Attempt(budget.iterations, name, args.get("sql"),
                            canonical_hash(args["sql"]) if args.get("sql") else None,
                            0, True, ErrorClass.NONE, "")
    except BaseException as e:  # noqa: BLE001
        c = classify_exception(e)
        if "not granted" in str(e) and config.detect_blockers:
            c = Classification(ErrorClass.HARD_BLOCKER, str(e),
                               feedback="that table cannot be read by this agent")
            state.blocked = c
        if verbose:
            print(f"  [{budget.iterations}] {name} -> {c.kind.value}")
        fb = (c.feedback or str(e)) if config.classify_errors else f"Error: {e}"
        return fb, Attempt(budget.iterations, name, args.get("sql"),
                           None, 0, False, c.kind, str(e))


def _finish(state, budget, decision, question, question_id, started, config) -> Outcome:
    outcome = Outcome(
        question_id=question_id,
        question=question,
        exit_reason=decision.reason.value,
        iterations=budget.iterations,
        tokens=budget.tokens,
        usd=budget.usd,
        seconds=time.monotonic() - started,
        answer_sql=state.answer_sql,
        answer_rows=state.answer_rows[:50],
        max_rung=state.max_rung,
        attempts=[asdict(a) | {"error_class": a.error_class.value} for a in state.attempts],
        detail=decision.detail,
    )
    # The single most useful field in the whole trace.
    update_trace(metadata=outcome.summary() | {"arm": config.label},
                 tags=[config.label, outcome.exit_reason])
    return outcome

"""The naive loop. This is the before picture, and it is not a strawman.

It is what most agent loops actually look like in production:

  while iteration < MAX:
      call the model
      run whatever SQL it gives back
      if that errored, paste the error string back
      if the model says it is done, believe it

Two exit conditions, both weak. The model controls one of them, so all the
pressure lands there. The other is a fuse, not a budget.

Differences from loop.py, and only these:

  1. submit_answer is trusted. No verifier, no gate. The model says done.
  2. Error feedback is the raw string. No classification, no schema, no
     statement of whether recovery is even possible.
  3. Hard blockers are invisible. A denied table produces an error like any
     other, so the agent retries it until the fuse blows.
  4. No progress detection. The same query three times in a row is fine.
  5. Budget is iterations only.

Everything else, model, tools, schema, prompt, is identical, so the difference
in the numbers is attributable to the loop rather than to anything else.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Optional

from agent import llm
from agent.classify import ErrorClass
from agent.db import connect, schema_map
from agent.gate import Attempt, Budget, ExitReason, State
from agent.llm import track, update_trace
from agent.loop import Outcome
from agent.tools import TOOL_SPECS, dispatch
from agent.verify import Invariants, canonical_hash, parse, verify

MAX_ITERATIONS = 12


@track(name="sql_agent_run_naive")
def run(question: str, question_id: str = "", invariants: Optional[Invariants] = None,
        budget: Optional[Budget] = None, conn=None, verbose: bool = False) -> Outcome:
    conn = conn or connect()
    schema = schema_map(conn)
    budget = budget or Budget(max_iterations=MAX_ITERATIONS)
    state = State(question=question)
    started = time.monotonic()
    messages = [{"role": "user", "content": question}]
    exit_reason = ExitReason.BUDGET_EXHAUSTED

    while budget.iterations < budget.max_iterations:
        try:
            turn = llm.propose_action(messages, TOOL_SPECS)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001
            # Charge the iteration even though the call failed. Without this
            # the counter never advances, the while condition can never go
            # false, and a bad key is an infinite loop rather than a run that
            # exhausts its budget.
            budget.charge(tokens=0, usd=0.0, iteration=True)
            time.sleep(2.0)
            continue

        budget.charge(tokens=turn.tokens, usd=turn.usd, iteration=True)
        messages.append({"role": "assistant", "content": turn.raw_content})

        if not turn.tool_calls:
            # The model stopped talking, so we call it done. This is exit
            # condition number one, and the model owns it.
            exit_reason = ExitReason.MODEL_SAID_DONE
            break

        results, done = [], False
        for call in turn.tool_calls:
            name, args = call["name"], call.get("input") or {}
            if name == "submit_answer":
                sql = args.get("sql", "")
                state.answer_sql = sql
                # Trusted. No verification. Recorded only so the offline
                # comparison in evals/ has something to grade.
                try:
                    rows, cols, _ = _peek(conn, sql)
                    state.answer_rows, state.answer_columns = rows, cols
                except Exception:
                    pass
                state.record(Attempt(budget.iterations, name, sql, canonical_hash(sql),
                                     0, True, ErrorClass.NONE, "trusted"))
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": "Answer recorded."})
                done = True
                continue

            try:
                out = dispatch(name, args, conn)
                # Fingerprint exploratory queries too. The engineered loop
                # does, and measuring only one of them makes the redundant
                # action rate an artefact of instrumentation, not behaviour.
                state.record(Attempt(
                    budget.iterations, name, args.get("sql"),
                    canonical_hash(args["sql"]) if args.get("sql") else None,
                    0, True, ErrorClass.NONE, ""))
            except BaseException as e:  # noqa: BLE001
                # The raw error, unclassified. A permission denial reads the
                # same as a typo, so the agent tries again.
                out = f"Error: {e}"
                state.record(Attempt(
                    budget.iterations, name, args.get("sql"),
                    canonical_hash(args["sql"]) if args.get("sql") else None,
                    0, False, ErrorClass.SEMANTIC, str(e)))
                if verbose:
                    print(f"  [{budget.iterations}] {name} -> error: {str(e)[:60]}")
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": out})

        messages.append({"role": "user", "content": results})
        if done:
            exit_reason = ExitReason.MODEL_SAID_DONE
            break

    outcome = Outcome(
        question_id=question_id,
        question=question,
        exit_reason=exit_reason.value,
        iterations=budget.iterations,
        tokens=budget.tokens,
        usd=budget.usd,
        seconds=time.monotonic() - started,
        answer_sql=state.answer_sql,
        answer_rows=state.answer_rows[:50],
        max_rung=0,
        attempts=[asdict(a) | {"error_class": a.error_class.value}
                  for a in state.attempts],
        detail="",
    )
    update_trace(metadata=outcome.summary(), tags=["naive", outcome.exit_reason])
    return outcome


def _peek(conn, sql):
    """Record what the agent's submitted query returns, for the trace only.

    Enforces the same table denials as run_query. Without that check this
    reaches the database on an unrestricted connection, so a query against a
    denied table records rows the agent was never able to see, and the trace
    reads as though it got in. Grading never uses this field: metrics.py
    re-executes answer_sql itself.
    """
    from agent.tools import DENIED_TABLES, PermissionDenied
    from agent.verify import execute_bounded, referenced_tables

    tree = parse(sql)
    denied = referenced_tables(tree) & DENIED_TABLES
    if denied:
        raise PermissionDenied(
            f"read access to {', '.join(sorted(denied))} is not granted to this agent"
        )
    return execute_bounded(conn, sql, timeout_s=10, max_rows=200)

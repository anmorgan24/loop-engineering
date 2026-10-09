"""End-to-end test of the loop with a scripted model. No API key needed.

    python -m evals.smoke

Every exit reason is reachable here, which makes this both the test suite and
a working offline demo. If the venue wifi dies, this still runs.

The scripted model replaces exactly one function, `llm.propose_action`. That it
can be swapped out this cleanly is the argument of the talk restated as a fact
about the code: the model is one transition, and the rest is ordinary software.
"""

from __future__ import annotations

import itertools
import os
import sys

# Scripted runs are not real traces. Keep them out of the Opik project, where
# they would sit next to corpus runs and skew the exit reason distribution.
os.environ.setdefault("OPIK_TRACK_DISABLE", "true")

from agent import llm, loop  # noqa: E402
from agent.db import connect
from agent.gate import Budget
from agent.verify import Invariants

REVENUE_CORRECT = """SELECT round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
FROM order_items i JOIN orders o USING(order_id) WHERE o.status = 'completed'"""

REVENUE_LISTPRICE = """SELECT round(sum(i.quantity * p.list_price), 2) AS revenue
FROM order_items i JOIN orders o USING(order_id) JOIN products p USING(product_id)
WHERE o.status = 'completed'"""

REVENUE_ALL_STATUSES = """SELECT round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
FROM order_items i"""

REVENUE_BAND = Invariants(expected_columns=1, value_band=(0, 2_150_000, 2_330_000))


def scripted(*turns):
    """Build a stand-in for llm.propose_action that replays canned turns."""
    seq = itertools.chain(turns, itertools.repeat(turns[-1]))

    def fake(messages, tools, system=None, max_tokens=1500):
        name, args = next(seq)
        calls = ([] if name is None
                 else [{"id": f"t{len(messages)}", "name": name, "input": args}])
        return llm.ModelTurn(
            text="", tool_calls=calls, input_tokens=900, output_tokens=120,
            raw_content=[{"type": "tool_use", "id": f"t{len(messages)}",
                          "name": name, "input": args}] if calls else [],
            stop_reason="tool_use" if calls else "end_turn",
        )
    return fake


SCENARIOS = [
    (
        "clean solve after exploring",
        "verified_success",
        REVENUE_BAND,
        scripted(
            ("list_tables", {}),
            ("describe_table", {"table": "order_items"}),
            ("submit_answer", {"sql": REVENUE_CORRECT}),
        ),
    ),
    (
        "rung 5 catches the list_price mistake, model repairs",
        "verified_success",
        REVENUE_BAND,
        scripted(
            ("submit_answer", {"sql": REVENUE_LISTPRICE}),
            ("submit_answer", {"sql": REVENUE_CORRECT}),
        ),
    ),
    (
        "rung 5 catches the forgotten status filter",
        "verified_success",
        REVENUE_BAND,
        scripted(
            ("submit_answer", {"sql": REVENUE_ALL_STATUSES}),
            ("submit_answer", {"sql": REVENUE_CORRECT}),
        ),
    ),
    (
        "same wrong query three times",
        "no_progress",
        REVENUE_BAND,
        scripted(("submit_answer", {"sql": REVENUE_LISTPRICE})),
    ),
    (
        "restricted table, noticed immediately",
        "hard_blocker",
        Invariants(),
        scripted(("submit_answer", {"sql": "SELECT method, count(*) AS n FROM payments GROUP BY 1"})),
    ),
    (
        "restricted table via an exploratory query",
        "hard_blocker",
        Invariants(),
        scripted(("run_query", {"sql": "SELECT * FROM payments LIMIT 5"})),
    ),
    (
        "hallucinated column, then recovery",
        "verified_success",
        Invariants(expected_columns=1, value_band=(0, 200, 200)),
        scripted(
            ("submit_answer", {"sql": "SELECT count(*) AS n FROM customers WHERE custmoer_id > 0"}),
            ("submit_answer", {"sql": "SELECT count(*) AS n FROM customers"}),
        ),
    ),
    (
        # The point is the negative: the naive loop reports this as
        # model_said_done and hands back no answer at all. Here it escalates,
        # because an exit the model controls is not an exit.
        "model stops talking, which is escalation and not success",
        "escalate",
        REVENUE_BAND,
        scripted((None, {})),
    ),
    (
        "exploration forever, budget stops it",
        "budget_exhausted",
        REVENUE_BAND,
        scripted(("list_tables", {})),
    ),
    (
        "unknown tool three times",
        "escalate",
        REVENUE_BAND,
        scripted(("query_the_database", {"sql": "SELECT 1"})),
    ),
]


def ablation_checks(conn) -> int:
    """Prove each flag changes the outcome it claims to.

    A flag that silently does nothing produces an ablation table full of
    identical columns, and you would not notice until someone asked about it
    on stage.
    """
    from agent.config import ARMS

    cases = [
        # (arm, scripted turns, invariants, expected exit, what it demonstrates)
        ("no_invariants",
         scripted(("submit_answer", {"sql": REVENUE_LISTPRICE})),
         REVENUE_BAND, "verified_success",
         "without rung 5 the wrong revenue is accepted"),
        ("engineered",
         scripted(("submit_answer", {"sql": REVENUE_LISTPRICE})),
         REVENUE_BAND, "no_progress",
         "with rung 5 it is not"),
        ("no_blocker_check",
         scripted(("submit_answer", {"sql": "SELECT method FROM payments"})),
         Invariants(), "no_progress",
         "without blocker detection the agent keeps retrying"),
        ("engineered",
         scripted(("submit_answer", {"sql": "SELECT method FROM payments"})),
         Invariants(), "hard_blocker",
         "with it, the run stops at once"),
        ("no_progress_check",
         scripted(("submit_answer", {"sql": REVENUE_LISTPRICE})),
         REVENUE_BAND, "budget_exhausted",
         "without progress detection the fuse blows instead"),
    ]

    failures = 0
    print("  ablation")
    for arm, fake, inv, expected, why in cases:
        llm.propose_action = fake
        out = loop.run(question="test", question_id="abl", invariants=inv,
                       budget=Budget(max_iterations=6), conn=conn, config=ARMS[arm])
        ok = out.exit_reason == expected
        failures += not ok
        mark = "ok  " if ok else "FAIL"
        got = "" if ok else f"  (got {out.exit_reason})"
        print(f"  {mark} {arm:<18} {why:<48} it={out.iterations}{got}")
    return failures


def unit_checks() -> int:
    """Properties the article states about the code, checked rather than assumed."""
    import subprocess

    from agent.verify import canonical_hash
    from evals.metrics import behaviour_correct, outcome_class
    from outer.route import Route
    from outer.route import route as outer_route
    from outer.dispatch import Task, dispatch

    def _raises(fn, exc_type) -> bool:
        try:
            fn()
            return False
        except exc_type:
            return True
        except Exception:
            return False

    def _dispatcher_no_progress_check() -> bool:
        """Dispatcher retries once on no_progress and reaches HUMAN_REVIEW.

        REVENUE_BAND causes REVENUE_LISTPRICE to fail rung 5, so the model
        keeps repeating the same failing query until no_progress fires.
        """
        conn = connect()
        llm.propose_action = scripted(("submit_answer", {"sql": REVENUE_LISTPRICE}))
        records = dispatch(
            [Task("smoke_np", "test question", invariants=REVENUE_BAND)],
            conn=conn,
            config=None,  # uses ENGINEERED, which detects no_progress
        )
        r = records[0]
        return len(r.outcomes) == 2 and r.final_route is Route.HUMAN_REVIEW

    a = "select o.order_id from orders o"
    checks = [
        ("fingerprint ignores case and whitespace",
         canonical_hash(a) == canonical_hash("SELECT   O.ORDER_ID\nFROM orders O")),
        ("fingerprint does not ignore alias renames (documented)",
         canonical_hash(a) != canonical_hash("select x.order_id from orders x")),
        ("fingerprint is stable across processes",
         len({subprocess.run([sys.executable, "-c",
              f"from agent.verify import canonical_hash; print(canonical_hash({a!r}))"],
              capture_output=True, text=True).stdout for _ in range(2)}) == 1),
        ("budget exhaustion is not noticing",
         not behaviour_correct("unanswerable", "budget_exhausted")),
        ("no_progress on a blocked task is noticing",
         behaviour_correct("blocked", "no_progress")),
        ("outcome buckets: giving up is not false success",
         outcome_class(False, "no_progress") == "stopped_unsolved"),
        ("outcome buckets: wrong and claimed is false success",
         outcome_class(False, "verified_success") == "false_success"),
        ("route returns Route for all known exit reasons",
         all(isinstance(outer_route(r, 1), Route)
             for r in ["verified_success", "model_said_done", "hard_blocker",
                       "no_progress", "budget_exhausted", "escalate"])),
        ("route raises ValueError on unknown exit reason",
         _raises(lambda: outer_route("alien_exit", 1), ValueError)),
        ("no_progress: RETRY on attempt 1, HUMAN_REVIEW on attempt 2",
         outer_route("no_progress", 1) is Route.RETRY
         and outer_route("no_progress", 2) is Route.HUMAN_REVIEW),
        ("dispatcher retries once on no_progress and reaches HUMAN_REVIEW",
         _dispatcher_no_progress_check()),
    ]
    failures = 0
    print("  units")
    for label, ok in checks:
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    return failures


N_UNIT_CHECKS = 11


def main() -> int:
    conn = connect()
    failures = 0
    print()
    for label, expected, inv, fake in SCENARIOS:
        llm.propose_action = fake  # the one swap
        out = loop.run(question="test", question_id="smoke", invariants=inv,
                       budget=Budget(max_iterations=6), conn=conn)
        ok = out.exit_reason == expected
        failures += not ok
        mark = "ok  " if ok else "FAIL"
        got = "" if ok else f"  (got {out.exit_reason})"
        print(f"  {mark} {label:<52} -> {expected:<18} it={out.iterations}{got}")

    print()
    failures += ablation_checks(conn)
    print()
    failures += unit_checks()

    total = len(SCENARIOS) + 5 + N_UNIT_CHECKS
    print(f"\n  {total - failures}/{total} checks passed\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

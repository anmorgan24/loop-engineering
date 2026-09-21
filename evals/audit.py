"""Audit the question suite before trusting any number it produces.

    python -m evals.audit

Three classes of bug this catches, all of which were in the first draft of
questions.py and none of which were visible in the results:

  TIE          the gold query uses LIMIT n and rows n and n+1 are tied on the
               ordering column, so the question has several right answers and
               the grader accepts only the one the gold happened to pick.

  AMBIGUOUS    the question does not pin the output shape, so a correct answer
               in the wrong presentation grades as wrong. A label the agent
               invents ("Unassigned" vs "unassigned"), a month rendered as an
               integer instead of a date, a share given as a fraction instead
               of a percent.

  WEAK         the invariants would accept an obviously wrong answer, so rung
               5 is decorative and the false success rate is whatever the
               model happens to do.

The first two inflate the failure rate and make the loop look worse than it
is. The third inflates the false success rate and makes the verifier look
better than it is. Both directions are worth finding before they are on a
slide.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from agent.db import connect, schema_map
from agent.verify import execute_bounded, verify
from evals.questions import ANSWERABLE, QUESTIONS

# Wording that pins the output shape. A question whose gold returns a value
# needing one of these should say so.
SHAPE_WORDS = ["exactly", "as a percent", "return", "label", "call it",
               "one row", "columns", "format", "name it", "0 to 100"]


def boundary_tie(conn, q) -> str | None:
    """Does the gold query cut a tie with LIMIT?"""
    try:
        tree = sqlglot.parse_one(q.gold_sql, dialect="duckdb")
    except Exception:
        return None
    lim = tree.find(exp.Limit)
    order = tree.find(exp.Order)
    if lim is None or order is None:
        return None
    try:
        n = int(lim.expression.name)
    except Exception:
        return None

    stripped = tree.copy()
    for node in stripped.find_all(exp.Limit):
        node.pop()
    try:
        rows, _, _ = execute_bounded(conn, stripped.sql(dialect="duckdb"), timeout_s=15)
    except Exception:
        return None
    if len(rows) <= n:
        return None

    # The ordering column is whatever the first ORDER BY term selects. Compare
    # the last included row against the first excluded one on every column
    # except the one that only exists to break the tie.
    key = len(rows[0]) - 1
    if rows[n - 1][key] == rows[n][key]:
        tied = sum(1 for r in rows if r[key] == rows[n - 1][key])
        return (f"LIMIT {n} cuts a {tied}-way tie at value {rows[n-1][key]!r}. "
                f"The question has several right answers.")
    return None


def ambiguous_shape(conn, q) -> str | None:
    """Would a correct answer in a different presentation grade as wrong?"""
    try:
        rows, cols, _ = execute_bounded(conn, q.gold_sql, timeout_s=15)
    except Exception:
        return None
    if not rows:
        return None

    said = any(w in q.text.lower() for w in SHAPE_WORDS)
    issues = []

    # A free-text literal the gold invents, such as a label for a null group.
    if "coalesce" in q.gold_sql.lower() and not said:
        issues.append("gold invents a literal label (coalesce) the question does not specify")

    # A date bucket that could reasonably be an int or a string.
    if "date_trunc" in q.gold_sql.lower() and not said:
        issues.append("gold buckets dates; month could be a date, an int or a string")

    # A percentage, which could reasonably be a fraction.
    if "100.0" in q.gold_sql and "percent" not in q.text.lower():
        issues.append("gold returns a percent; the question does not say percent or fraction")

    # "sold", "revenue", "bought" over orders without a status filter. In this
    # schema that has three defensible readings (all rows, exclude cancelled,
    # completed only) and they give different answers.
    sales_word = any(w in q.text.lower()
                     for w in ["sold", "revenue", "bought", "purchased", "sales"])
    touches_orders = "order" in q.gold_sql.lower()
    filters_status = "status" in q.gold_sql.lower()
    if sales_word and touches_orders and not filters_status:
        issues.append("a sales question over orders with no status filter; "
                      "all-rows, exclude-cancelled and completed-only differ")

    # More than two columns invites the agent to return a different subset.
    if len(cols) > 2 and not said:
        issues.append(f"gold returns {len(cols)} columns; the question does not say which")

    return "; ".join(issues) or None


def weak_invariants(q) -> str | None:
    inv = q.invariants
    has = [n for n, v in [("expected_columns", inv.expected_columns),
                          ("row_band", inv.row_band),
                          ("value_band", inv.value_band),
                          ("reconcile_sql", inv.reconcile_sql)] if v is not None]
    if not has:
        return "no invariants at all; rung 5 can only check for empty and all-null"
    if has == ["expected_columns"]:
        return "only a column count; almost any wrong answer passes"
    return None


def main() -> int:
    conn = connect()
    schema = schema_map(conn)
    problems = 0

    print("\n  auditing", len(QUESTIONS), "questions\n")
    for q in QUESTIONS:
        findings = []
        if q.gold_sql:
            r = verify(q.gold_sql, conn, schema, q.invariants)
            if not r.ok:
                findings.append(("BROKEN", f"gold fails its own invariants: {r.detail[:70]}"))
            for kind, fn in [("TIE", boundary_tie), ("AMBIGUOUS", ambiguous_shape)]:
                msg = fn(conn, q)
                if msg:
                    findings.append((kind, msg))
        w = weak_invariants(q) if q.gold_sql else None
        if w:
            findings.append(("WEAK", w))

        if findings:
            problems += 1
            print(f"  {q.id} {q.tier}")
            for kind, msg in findings:
                print(f"      {kind:<10} {msg}")

    clean = len(QUESTIONS) - problems
    print(f"\n  {clean}/{len(QUESTIONS)} questions clean, {problems} need attention\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

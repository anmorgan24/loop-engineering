"""The verifier ladder.

Verification is not one check. It is a sequence, cheapest and most
deterministic first, where each rung catches a different class of error.

    rung 1  PARSE       the string is SQL
    rung 2  RESOLVE     every table and column it names exists
    rung 3  PLAN        the engine accepts the plan
    rung 4  EXECUTE     it runs inside a time and row budget
    rung 5  INVARIANTS  the result is not obviously wrong

Rungs 1 to 4 are free and nearly everyone already has them. They are also the
rungs that catch the errors that would have been obvious anyway. Every
interesting failure in this schema, discounts ignored, cancelled orders
included, customers dropped by an inner join, clears rungs 1 to 4 cleanly and
is only visible at rung 5.

Rung 6 exists but is not in this file. Comparing against a gold result set is
an offline evaluation concern, not a runtime gate: at runtime you do not have
the answer. Keeping the two ladders separate is the point. See evals/metrics.py.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, IntEnum
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

from agent.db import DENIED_TABLES

DIALECT = "duckdb"


class Rung(IntEnum):
    PARSE = 1
    RESOLVE = 2
    PLAN = 3
    EXECUTE = 4
    INVARIANTS = 5


@dataclass
class Invariants:
    """Per-question expectations, split by what they need to exist.

    ANSWER-FREE invariants are derivable from the question and the schema
    alone. You can write them before anyone has computed the answer, and they
    are honest at runtime, where by definition you do not have it.

        expected_columns   the question says how many columns
        row_band           the question says how many rows ("exactly five")
        reconcile_sql      a second, independently written query that must agree
        reconcile_sum      the returned rows must sum to an independent total
        allow_empty        zero rows is a legitimate answer here

    CALIBRATED invariants need history or domain knowledge: last month's
    revenue, a known plausible range. They are legitimate in production, where
    you have prior numbers. They are NOT legitimate when derived from the
    answer key of the suite you are scoring, which is cheating with extra
    steps.

        value_band         a magnitude range for one column

    The arms in agent/config.py let you score with and without the calibrated
    set, so the gap between them is visible rather than hidden.
    """
    # answer-free
    expected_columns: Optional[int] = None
    row_band: Optional[tuple[int, int]] = None
    # calibrated
    value_band: Optional[tuple[int, float, float]] = None  # (col_index, lo, hi)
    reconcile_sql: Optional[str] = None
    # (column_index, control_sql): the sum of that column across every returned
    # row must match the control scalar. This is the strongest invariant in the
    # file and the only one that works on grouped results, where reconcile_sql
    # can only see the first cell. It is what catches an inner join that
    # silently dropped a group: the parts no longer add up to the whole.
    reconcile_sum: Optional[tuple] = None
    reconcile_tolerance: float = 0.01
    allow_empty: bool = False


@dataclass
class VerifierResult:
    rung: Rung
    ok: bool
    detail: str = ""
    rows: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    truncated: bool = False
    # Which cause the empty-result probe pointed at, when it ran.
    empty_cause: Optional[str] = None
    # The same failure stated without any guidance. This is what a loop that
    # does not invest in feedback sends back, and the no_feedback arm uses it
    # to measure what the guidance is worth.
    raw_detail: str = ""

    @property
    def reached(self) -> int:
        """Highest rung reached, whether or not it passed.

        Tracking this per iteration is what makes no-progress detection work:
        an agent stuck at rung 2 for three iterations does not understand the
        schema, and more attempts will not fix that.
        """
        return int(self.rung)


class ExecutionTimeout(Exception):
    pass


class PermissionDenied(Exception):
    pass


# --------------------------------------------------------------------------
# rung 1
# --------------------------------------------------------------------------

def parse(sql: str) -> exp.Expression:
    tree = sqlglot.parse_one(sql, dialect=DIALECT)
    if tree is None:
        raise ParseError("empty statement")
    # A read-only agent that emits DML is a malformed action, not a semantic
    # failure. Catching it here keeps it out of the engine entirely.
    if not isinstance(tree, exp.Query):
        raise ParseError(f"only read queries are permitted, got {type(tree).__name__}")
    return tree


def canonical_hash(sql: str) -> str:
    """Normalised fingerprint, used for no-progress detection.

    Two queries that differ only in whitespace, alias names, or keyword case
    are the same action. An agent that resubmits one has made no progress no
    matter how much its reasoning text claims otherwise.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
        normalised = tree.sql(dialect=DIALECT, normalize=True, pretty=False, comments=False)
    except ParseError:
        normalised = " ".join(sql.lower().split())
    return str(hash(normalised))


# --------------------------------------------------------------------------
# rung 2
# --------------------------------------------------------------------------

def cte_names(tree: exp.Expression) -> set[str]:
    """Names bound by WITH clauses. These are not base tables.

    Without this, every CTE looks like a missing table. Agent-written SQL uses
    CTEs constantly, so getting this wrong rejects a large share of correct
    queries at rung 2 and sends the loop chasing a problem that is not there.
    """
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}


def referenced_tables(tree: exp.Expression) -> set[str]:
    """Base tables the query reads, excluding anything bound by a WITH clause."""
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name} - cte_names(tree)


def resolve(tree: exp.Expression, schema: dict[str, list]) -> None:
    """Raise if the query names a table or column that does not exist.

    This runs before the engine sees the query, so a hallucinated column costs
    nothing and produces a message that names the real columns.
    """
    denied = referenced_tables(tree) & DENIED_TABLES
    if denied:
        raise PermissionDenied(
            f"read access to {', '.join(sorted(denied))} is not granted to this agent"
        )

    unknown = referenced_tables(tree) - set(schema)
    if unknown:
        raise ValueError(
            f"unknown table(s): {', '.join(sorted(unknown))}. "
            f"available: {', '.join(sorted(schema))}"
        )

    flat = {t: {c.name: c.dtype for c in cols} for t, cols in schema.items()}
    try:
        qualify(tree.copy(), schema=flat, dialect=DIALECT, validate_qualify_columns=True)
    except Exception as e:
        raise ValueError(_readable_resolve_error(str(e), tree, schema)) from e


def _readable_resolve_error(msg: str, tree: exp.Expression, schema: dict) -> str:
    """Turn a resolver error into something the model can act on.

    What you feed back matters more than whether you feed back. The raw message
    names the failure; this one names the available columns, which is the
    information needed to fix it. The recovery rate difference between these
    two strings is measurable, and it is measured in evals/metrics.py.
    """
    tables = sorted(referenced_tables(tree) & set(schema))
    if not tables:
        return msg
    catalogue = "; ".join(
        f"{t}({', '.join(c.name for c in schema[t])})" for t in tables
    )
    return f"{msg} | columns available in the tables you referenced: {catalogue}"


# --------------------------------------------------------------------------
# rungs 3 and 4
# --------------------------------------------------------------------------

def plan_ok(conn, sql: str) -> None:
    conn.execute(f"EXPLAIN {sql}").fetchall()


def execute_bounded(conn, sql: str, timeout_s: float = 10.0, max_rows: int = 50_000):
    """Run with a wall clock budget and a row cap.

    Budget is not only tokens. An agent that writes an accidental cross join
    will happily wait forever, and wall clock is the dimension that catches it.
    """
    box: dict[str, Any] = {}

    def run():
        try:
            cur = conn.execute(sql)
            box["columns"] = [d[0] for d in cur.description] if cur.description else []
            box["rows"] = cur.fetchmany(max_rows + 1)
        except BaseException as e:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        try:
            conn.interrupt()
        except Exception:
            pass
        raise ExecutionTimeout(f"query exceeded {timeout_s:.0f}s and was cancelled")
    if "error" in box:
        raise box["error"]

    rows = box.get("rows", [])
    truncated = len(rows) > max_rows
    return rows[:max_rows], box.get("columns", []), truncated


# --------------------------------------------------------------------------
# rung 5
# --------------------------------------------------------------------------

def _strip_predicates(tree: exp.Expression) -> Optional[str]:
    """Return the query with its top level WHERE and HAVING removed."""
    relaxed = tree.copy()
    found = False
    for node in list(relaxed.find_all(exp.Where)) + list(relaxed.find_all(exp.Having)):
        if node.parent is relaxed or node.parent_select is relaxed:
            node.pop()
            found = True
    return relaxed.sql(dialect=DIALECT) if found else None


class EmptyCause(str, Enum):
    NO_PREDICATES = "no_predicates"
    UPSTREAM = "relaxed_also_empty"
    PREDICATE = "predicate_eliminated_rows"


def probe_empty(conn, tree: exp.Expression) -> EmptyCause:
    """Localise the cause of an empty result set.

    An empty result means either "the answer really is zero rows" or "the query
    is broken". The tempting fix is to ask the model which one. The better move
    is deterministic: strip the predicates and look again.

    Be precise about what this buys. The probe does not decide whether zero is
    the right answer, because nothing at runtime can. It narrows the suspect:

      relaxed query also empty -> the join or the base table is at fault,
                                  the predicate is not
      relaxed query has rows   -> the predicate eliminated everything, which
                                  may well be correct

    That is still worth having, because the two cases need different feedback
    and sending the wrong one costs iterations. When zero genuinely is the
    expected answer for a question, the question says so with allow_empty, and
    the gate never reaches this probe.

    Ambiguous verifier outcomes need a policy. The policy should not be a
    model call.
    """
    relaxed_sql = _strip_predicates(tree)
    if relaxed_sql is None:
        return EmptyCause.NO_PREDICATES
    try:
        rows, _, _ = execute_bounded(conn, f"SELECT * FROM ({relaxed_sql}) LIMIT 1", timeout_s=5)
    except Exception:
        return EmptyCause.NO_PREDICATES
    return EmptyCause.UPSTREAM if not rows else EmptyCause.PREDICATE


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def check_invariants(conn, tree, rows, columns, inv: Invariants,
                     use_calibrated: bool = True) -> VerifierResult:
    def fail(detail: str, raw: str = "", **kw) -> VerifierResult:
        return VerifierResult(Rung.INVARIANTS, False, detail, rows, columns,
                              raw_detail=raw or "result failed validation", **kw)

    if not rows:
        if inv.allow_empty:
            return VerifierResult(Rung.INVARIANTS, True,
                                  "empty result, and zero rows is an accepted answer here",
                                  rows, columns, empty_cause=None)
        cause = probe_empty(conn, tree)
        messages = {
            EmptyCause.UPSTREAM: (
                "empty result, and the same query with its WHERE/HAVING removed is "
                "also empty. The join or the base table is the suspect, not the "
                "predicate. Check join keys and whether a join dropped every row."
            ),
            EmptyCause.PREDICATE: (
                "empty result, and the same query without its WHERE/HAVING returns "
                "rows. The predicate eliminated everything. Check literal spelling "
                "against the real column values, and check date ranges."
            ),
            EmptyCause.NO_PREDICATES: (
                "empty result with no predicate to relax. The base table or the "
                "join produced nothing."
            ),
        }
        return fail(messages[cause], raw="empty result", empty_cause=cause.value)

    if inv.expected_columns is not None and len(columns) != inv.expected_columns:
        return fail(f"expected {inv.expected_columns} column(s), got {len(columns)}: {columns}",
                        raw="wrong number of columns")

    for i, name in enumerate(columns):
        if all(r[i] is None for r in rows):
            return fail(f"column '{name}' is NULL in every row, which is almost never intended",
                            raw="a column is entirely NULL")

    if inv.row_band is not None:
        lo, hi = inv.row_band
        if not lo <= len(rows) <= hi:
            return fail(
                f"returned {len(rows)} rows, expected between {lo} and {hi}. "
                "A count far above the band usually means join fan-out.",
                raw="wrong number of rows",
            )

    if inv.value_band is not None and use_calibrated:
        idx, lo, hi = inv.value_band
        v = _as_float(rows[0][idx]) if rows and idx < len(rows[0]) else None
        if v is None:
            return fail(f"expected a numeric value in column {idx}, got {rows[0][idx]!r}")
        if not lo <= v <= hi:
            return fail(
                f"value {v:,.2f} is outside the plausible band {lo:,.0f} to {hi:,.0f}. "
                "Check the price column, the status filter, and the join grain.",
                raw="value out of range",
            )

    if inv.reconcile_sum is not None:
        idx, control_sql = inv.reconcile_sum
        try:
            ctrl, _, _ = execute_bounded(conn, control_sql, timeout_s=10)
        except Exception as e:
            return fail(f"reconciliation control query failed: {e}")
        want = _as_float(ctrl[0][0]) if ctrl else None
        parts = [_as_float(r[idx]) for r in rows if idx < len(r)]
        if want is None or any(p is None for p in parts):
            return fail(f"reconcile_sum needs numeric values in column {idx}")
        got = sum(parts)
        denom = abs(want) or 1.0
        if abs(got - want) / denom > inv.reconcile_tolerance:
            return fail(
                f"the returned rows sum to {got:,.2f} but the total is {want:,.2f}, "
                f"off by {abs(got-want)/denom:.1%}. A group is missing or double "
                "counted. Check for an inner join that dropped NULL keys.",
                raw="reconciliation failed",
            )

    if inv.reconcile_sql is not None:
        try:
            ctrl, _, _ = execute_bounded(conn, inv.reconcile_sql, timeout_s=10)
        except Exception as e:
            return fail(f"reconciliation control query failed: {e}")
        got, want = _as_float(rows[0][0]), _as_float(ctrl[0][0]) if ctrl else None
        if got is None or want is None:
            return fail("reconciliation needs a numeric first column in both queries")
        denom = abs(want) or 1.0
        drift = abs(got - want) / denom
        if drift > inv.reconcile_tolerance:
            return fail(
                f"result {got:,.2f} disagrees with an independent control query "
                f"({want:,.2f}), off by {drift:.1%}.",
                raw="reconciliation failed",
            )

    return VerifierResult(Rung.INVARIANTS, True, "all invariants hold", rows, columns)


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------

def verify(sql: str, conn, schema: dict, inv: Optional[Invariants] = None,
           stop_after: Rung = Rung.INVARIANTS,
           use_calibrated: bool = True) -> VerifierResult:
    """Run the ladder. `stop_after` truncates it, which is how the ablation
    arm measures what rung 5 is worth: stop at EXECUTE and the gate accepts
    anything the engine ran."""
    inv = inv or Invariants()

    try:
        tree = parse(sql)
    except ParseError as e:
        return VerifierResult(Rung.PARSE, False, str(e),
                              raw_detail="syntax error")

    try:
        resolve(tree, schema)
    except PermissionDenied as e:
        return VerifierResult(Rung.RESOLVE, False, f"PERMISSION_DENIED: {e}",
                              raw_detail="permission denied")
    except ValueError as e:
        # `str(e)` here already carries the column catalogue that
        # _readable_resolve_error appended. raw_detail is the version without
        # it, which is what most loops actually send.
        return VerifierResult(Rung.RESOLVE, False, str(e),
                              raw_detail=str(e).split(" | ")[0])

    try:
        plan_ok(conn, sql)
    except Exception as e:
        return VerifierResult(Rung.PLAN, False, str(e), raw_detail="planner rejected query")

    try:
        rows, columns, truncated = execute_bounded(conn, sql)
    except ExecutionTimeout as e:
        return VerifierResult(Rung.EXECUTE, False, str(e), raw_detail="query timed out")
    except Exception as e:
        return VerifierResult(Rung.EXECUTE, False, str(e), raw_detail="query failed")

    if truncated:
        return VerifierResult(
            Rung.EXECUTE, False,
            "query returned more than the 50,000 row cap; aggregate or narrow it",
            rows, columns, truncated=True,
        )

    if stop_after <= Rung.EXECUTE:
        return VerifierResult(Rung.EXECUTE, True, "executed", rows, columns)

    result = check_invariants(conn, tree, rows, columns, inv,
                              use_calibrated=use_calibrated)
    result.truncated = False
    return result

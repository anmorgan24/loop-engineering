"""Measurement, kept separate from control on purpose.

Nothing in this file may be called from inside the loop. The verifier ladder in
agent/verify.py gates the loop and stops at rung 5, because at runtime you do
not have the answer. Rung 6, comparing to a gold result set, lives here and
only runs offline.

The LLM judge at the bottom is the clearest statement of the split. It is a
legitimate metric and it is not allowed anywhere near `should_continue`.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from agent.verify import execute_bounded


# --------------------------------------------------------------------------
# rung 6: offline correctness
# --------------------------------------------------------------------------

def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), 2)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    # Case-folded: capitalisation is presentation, not correctness, and
    # "Unassigned" versus "unassigned" is not a wrong answer.
    return str(v).strip().casefold()


def _result_set(conn, sql: str) -> Optional[list[tuple]]:
    try:
        rows, _, _ = execute_bounded(conn, sql, timeout_s=15, max_rows=50_000)
    except Exception:
        return None
    return [tuple(_norm(v) for v in r) for r in rows]


def result_set_match(conn, answer_sql: Optional[str], gold_sql: Optional[str]) -> bool:
    """Order-insensitive comparison against the gold result set.

    Order-insensitive because two correct queries can disagree on ordering when
    the question does not specify one. Where the question does specify an order,
    the gold query's ORDER BY makes it observable in the rows themselves.
    """
    if answer_sql is None or gold_sql is None:
        return False
    got, want = _result_set(conn, answer_sql), _result_set(conn, gold_sql)
    if got is None or want is None:
        return False
    return Counter(got) == Counter(want)


def scores_for(conn, outcome, question) -> dict:
    """Grade one run. `correct` means different things for different tiers."""
    if question.tier in ("blocked", "unanswerable"):
        # There is no right answer. The right behaviour is to notice that and
        # stop, rather than to keep rewriting a query that cannot succeed.
        # "blocked" is a permission denial, which the agent is told about.
        # "unanswerable" is data that does not exist, which it has to work out.
        # Any exit that is not a claimed success counts as noticing.
        correct = outcome.exit_reason in (
            "hard_blocker", "no_progress", "escalate", "budget_exhausted")
    else:
        correct = result_set_match(conn, outcome.answer_sql, question.gold_sql)

    return {
        "correct": bool(correct),
        "exit_reason": outcome.exit_reason,
        "iterations": outcome.iterations,
        "usd": outcome.usd,
        "tokens": outcome.tokens,
        "seconds": outcome.seconds,
        "tier": question.tier,
        "question_id": question.id,
        "max_rung": outcome.max_rung,
        "attempts": outcome.attempts,
        # The gap that matters most: the loop thought it was done, and it was
        # wrong. A loop with no verifier has a large one by construction.
        "false_success": outcome.exit_reason in ("verified_success", "model_said_done")
                         and not correct,
    }


# --------------------------------------------------------------------------
# corpus level
# --------------------------------------------------------------------------

@dataclass
class CorpusReport:
    label: str
    n: int
    solve_rate: float
    false_success_rate: float
    exit_reasons: dict
    iters_p50: float
    iters_p95: float
    usd_total: float
    usd_per_solved: float
    usd_wasted: float
    recovery_by_class: dict
    redundant_action_rate: float
    blocked_handled: str


def _pct(x: float) -> float:
    return round(100 * x, 1)


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 1)


def summarise(label: str, scores: list[dict]) -> CorpusReport:
    n = len(scores)
    solved = [s for s in scores if s["correct"]]
    unsolved = [s for s in scores if not s["correct"]]

    # Recovery rate: given an error of class X appeared, did the run go on to
    # succeed? This is the number that moves when you improve feedback text.
    seen = defaultdict(int)
    recovered = defaultdict(int)
    redundant = 0
    total_query_attempts = 0

    for s in scores:
        classes = {a["error_class"] for a in s["attempts"] if not a["ok"]}
        for c in classes:
            seen[c] += 1
            if s["correct"]:
                recovered[c] += 1
        hashes = [a["sql_hash"] for a in s["attempts"] if a.get("sql_hash")]
        total_query_attempts += len(hashes)
        redundant += len(hashes) - len(set(hashes))

    blocked = [s for s in scores if s["tier"] == "blocked"]
    blocked_ok = sum(1 for s in blocked if s["exit_reason"] == "hard_blocker")

    return CorpusReport(
        label=label,
        n=n,
        solve_rate=_pct(len(solved) / n) if n else 0.0,
        false_success_rate=_pct(sum(s["false_success"] for s in scores) / n) if n else 0.0,
        exit_reasons=dict(Counter(s["exit_reason"] for s in scores).most_common()),
        iters_p50=_quantile([s["iterations"] for s in solved], 0.50),
        iters_p95=_quantile([s["iterations"] for s in solved], 0.95),
        usd_total=round(sum(s["usd"] for s in scores), 4),
        usd_per_solved=round(sum(s["usd"] for s in scores) / len(solved), 4) if solved else 0.0,
        usd_wasted=round(sum(s["usd"] for s in unsolved), 4),
        recovery_by_class={c: _pct(recovered[c] / seen[c]) for c in sorted(seen) if seen[c]},
        redundant_action_rate=_pct(redundant / total_query_attempts) if total_query_attempts else 0.0,
        blocked_handled=f"{blocked_ok}/{len(blocked)}" if blocked else "n/a",
    )


# --------------------------------------------------------------------------
# an Opik metric, for the experiment view
# --------------------------------------------------------------------------

try:
    from opik.evaluation.metrics import base_metric, score_result

    class ResultSetMatch(base_metric.BaseMetric):
        """Deterministic correctness. This is the metric that decides anything."""

        def __init__(self, conn, name: str = "result_set_match"):
            super().__init__(name=name)
            self._conn = conn

        def score(self, answer_sql=None, gold_sql=None, tier=None,
                  exit_reason=None, **_) -> score_result.ScoreResult:
            if tier == "blocked":
                ok = exit_reason == "hard_blocker"
                reason = ("stopped on the blocker" if ok
                          else f"kept going, exited as {exit_reason}")
            else:
                ok = result_set_match(self._conn, answer_sql, gold_sql)
                reason = "matches gold result set" if ok else "does not match gold"
            return score_result.ScoreResult(name=self.name, value=1.0 if ok else 0.0,
                                            reason=reason)

except ImportError:  # pragma: no cover
    ResultSetMatch = None  # type: ignore

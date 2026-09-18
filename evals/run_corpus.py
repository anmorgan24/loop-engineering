"""Run the whole suite through one loop and write the scores to disk.

    python -m evals.run_corpus --loop naive      # the before column
    python -m evals.run_corpus --loop engineered # the after column
    python -m evals.run_corpus --loop both

Results land in out/<loop>.json. report.py turns the pair into the table and
the chart. Nothing here needs Opik; with OPIK_API_KEY set you additionally get
the span tree per run, which is what you navigate live on stage.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

from agent import loop as engineered_loop
from agent import naive as naive_loop
from agent.db import connect
from agent.gate import Budget
from evals.metrics import scores_for, summarise
from evals.questions import QUESTIONS

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

RUNNERS = {"engineered": engineered_loop.run, "naive": naive_loop.run}


def run_one_corpus(which: str, limit: int | None = None, verbose: bool = True) -> dict:
    runner = RUNNERS[which]
    conn = connect()
    questions = QUESTIONS[:limit] if limit else QUESTIONS
    scores = []

    if os.environ.get("OPIK_API_KEY") or os.environ.get("OPIK_URL_OVERRIDE"):
        os.environ.setdefault("OPIK_PROJECT_NAME", f"sql-loop-{which}")

    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        try:
            outcome = runner(
                question=q.text,
                question_id=q.id,
                invariants=q.invariants,
                budget=Budget(),
                conn=conn,
            )
        except Exception as e:  # a crash is a result too; do not hide it
            print(f"  {q.id} CRASHED: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        s = scores_for(conn, outcome, q)
        s["question"] = q.text
        s["answer_sql"] = outcome.answer_sql
        s["gold_sql"] = q.gold_sql
        scores.append(s)

        if verbose:
            mark = "ok  " if s["correct"] else "MISS"
            print(f"  [{i:>2}/{len(questions)}] {q.id} {q.tier:<7} {mark} "
                  f"{outcome.exit_reason:<18} it={outcome.iterations:<2} "
                  f"${outcome.usd:.4f} {time.monotonic()-t0:.1f}s")

    OUT.mkdir(exist_ok=True)
    payload = {"loop": which, "scores": scores}
    (OUT / f"{which}.json").write_text(json.dumps(payload, indent=2, default=str))

    rep = summarise(which, scores)
    print(f"\n  {which}: solve {rep.solve_rate}%  false-success {rep.false_success_rate}%  "
          f"${rep.usd_total:.2f} total, ${rep.usd_wasted:.2f} on runs that failed")
    print(f"  exits: {rep.exit_reasons}\n")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", choices=["naive", "engineered", "both"], default="both")
    ap.add_argument("--limit", type=int, default=None, help="first N questions only")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set")

    targets = ["naive", "engineered"] if args.loop == "both" else [args.loop]
    for t in targets:
        print(f"\n=== {t} ===")
        run_one_corpus(t, limit=args.limit)

    print("wrote out/*.json  ->  now run: python -m evals.report")


if __name__ == "__main__":
    main()

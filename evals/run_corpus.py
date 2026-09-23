"""Run the suite through one or more loop variants, repeatedly.

    python -m evals.run_corpus --arm naive engineered --trials 3
    python -m evals.run_corpus --arm core --trials 3        # the three claims
    python -m evals.run_corpus --arm all --trials 3
    python -m evals.run_corpus --arm engineered --limit 3   # cost check

Results land in out/<arm>__t<n>.json, one file per arm per trial, so a crashed
trial costs you that trial and nothing else. Reruns skip files that already
exist, so an interrupted run resumes where it stopped.

Trials matter. A single run per task makes small gaps indistinguishable from
noise, and "the engineered loop scored four points higher" is not a claim you
want to defend from a stage on one sample.
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
from agent.config import ARMS, CORE_ARMS
from agent.db import connect
from agent.gate import Budget
from evals.metrics import scores_for, summarise
from evals.questions import QUESTIONS

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"
ALL_ARMS = ["naive"] + list(ARMS)


def result_path(arm: str, trial: int, limit=None) -> pathlib.Path:
    """Where an arm's results go.

    A --limit run gets its own name. Otherwise a three-task cost probe writes
    the same file as a full trial, and the resume check below then skips the
    real run because "trial 1 already exists". That happened, and the report
    quietly averaged a 3-task arm against 25-task arms.
    """
    if limit:
        return OUT / f"{arm}__limit{limit}.json"
    return OUT / f"{arm}__t{trial}.json"


def run_arm(arm: str, trial: int, limit=None, verbose: bool = True) -> list:
    questions = QUESTIONS[:limit] if limit else QUESTIONS
    conn = connect()
    scores = []

    if os.environ.get("OPIK_API_KEY"):
        os.environ["OPIK_PROJECT_NAME"] = f"sql-loop-{arm}"

    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        kwargs = dict(question=q.text, question_id=q.id, invariants=q.invariants,
                      budget=Budget(), conn=conn)
        try:
            if arm == "naive":
                outcome = naive_loop.run(**kwargs)
            else:
                outcome = engineered_loop.run(**kwargs, config=ARMS[arm])
        except Exception as e:
            print(f"    {q.id} CRASHED: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        s = scores_for(conn, outcome, q)
        s.update({"question": q.text, "answer_sql": outcome.answer_sql,
                  "gold_sql": q.gold_sql, "arm": arm, "trial": trial})
        scores.append(s)

        if verbose:
            mark = "ok  " if s["correct"] else "MISS"
            print(f"    [{i:>2}/{len(questions)}] {q.id} {q.tier:<7} {mark} "
                  f"{outcome.exit_reason:<18} it={outcome.iterations:<2} "
                  f"${outcome.usd:.4f} {time.monotonic()-t0:.1f}s")

    OUT.mkdir(exist_ok=True)
    result_path(arm, trial, limit).write_text(
        json.dumps({"arm": arm, "trial": trial, "n_tasks": len(questions),
                    "scores": scores}, indent=2, default=str))

    rep = summarise(arm, scores)
    print(f"    -> solve {rep.solve_rate}% (all {rep.n})  false-success {rep.false_success_rate}% (all {rep.n})  "
          f"${rep.usd_total:.2f}")
    return scores


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", nargs="+", default=["naive", "engineered"],
                    help="one or more arm names, or 'core', or 'all'")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="first N questions only")
    args = ap.parse_args()

    arms = args.arm
    if arms == ["all"]:
        arms = ALL_ARMS
    elif arms == ["core"]:
        arms = ["naive"] + CORE_ARMS
    unknown = [a for a in arms if a not in ALL_ARMS]
    if unknown:
        sys.exit(f"unknown arm(s): {unknown}. choose from {ALL_ARMS}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set")

    n_tasks = len(QUESTIONS[:args.limit] if args.limit else QUESTIONS)
    print(f"\n{len(arms)} arm(s) x {args.trials} trial(s) x {n_tasks} tasks "
          f"= {len(arms) * args.trials * n_tasks} runs\n")

    started = time.monotonic()
    for trial in range(1, args.trials + 1):
        for arm in arms:
            path = result_path(arm, trial, args.limit)
            if path.exists():
                done = len(json.loads(path.read_text())["scores"])
                if done == n_tasks:
                    print(f"  {arm} trial {trial}: already done, skipping")
                    continue
                print(f"  {arm} trial {trial}: found {done}/{n_tasks} tasks, rerunning")
            print(f"  === {arm}, trial {trial} ===")
            run_arm(arm, trial, limit=args.limit)

    print(f"\ndone in {(time.monotonic()-started)/60:.1f} min. "
          f"now run: python -m evals.report\n")


if __name__ == "__main__":
    main()

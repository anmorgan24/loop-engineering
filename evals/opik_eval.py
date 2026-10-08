"""Score result files through Opik's experiment framework.

Reads the existing result files from out/ and runs opik.evaluate with the
ResultSetMatch metric, so every run appears in the Opik experiment view with a
deterministic correctness score. No model is called: the answers come from the
result files that make core already produced.

    python -m evals.opik_eval                     # all arms, all trials
    python -m evals.opik_eval --arm engineered    # one arm only

Requires OPIK_API_KEY and OPIK_WORKSPACE. Traces land in the project named by
OPIK_PROJECT_NAME (default: loop-engineering, set in agent/llm.py).

The dataset is upserted on each run, so reruns are safe. Items are keyed by
arm + trial + question_id, which is unique across all result files.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

os.environ.setdefault("OPIK_PROJECT_NAME", "loop-engineering")

try:
    import opik
except ImportError:
    sys.exit("opik is not installed: pip install opik")

from agent.db import connect
from evals.metrics import ResultSetMatch

if ResultSetMatch is None:
    sys.exit("opik evaluation classes could not be loaded")

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"
DATASET_NAME = "loop-engineering-runs"


def _load_scores(arm: str | None) -> list[dict]:
    scores = []
    for path in sorted(OUT.glob("*__t*.json")):
        data = json.loads(path.read_text())
        if arm and data.get("arm") != arm:
            continue
        scores.extend(data["scores"])
    if not scores:
        sys.exit("no result files found in out/ -- run make core first")
    return scores


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", default=None,
                    help="score one arm only (default: all arms)")
    ap.add_argument("--experiment", default=None,
                    help="Opik experiment name (default: loop-engineering/<arm or all>)")
    args = ap.parse_args()

    if not os.environ.get("OPIK_API_KEY"):
        sys.exit("OPIK_API_KEY is not set")

    scores = _load_scores(args.arm)
    suffix = args.arm or "all"
    experiment_name = args.experiment or f"loop-engineering/{suffix}"
    print(f"  {len(scores)} runs -> dataset {DATASET_NAME!r}, experiment {experiment_name!r}")

    client = opik.Opik()
    dataset = client.get_or_create_dataset(DATASET_NAME)
    dataset.insert([
        {
            "input": {
                "question": s["question"],
                "answer_sql": s.get("answer_sql"),
                "gold_sql": s.get("gold_sql"),
                "tier": s["tier"],
                "exit_reason": s["exit_reason"],
            },
            "expected_output": {
                "gold_sql": s.get("gold_sql"),
            },
            "metadata": {
                "question_id": s["question_id"],
                "arm": s["arm"],
                "trial": s["trial"],
            },
        }
        for s in scores
    ])

    conn = connect()
    metric = ResultSetMatch(conn)

    def task(item: dict) -> dict:
        """Pass pre-computed fields through. No model is called."""
        inp = item["input"]
        return {
            "answer_sql": inp.get("answer_sql"),
            "gold_sql": inp.get("gold_sql"),
            "tier": inp["tier"],
            "exit_reason": inp["exit_reason"],
        }

    opik.evaluate(
        dataset=dataset,
        task=task,
        scoring_metrics=[metric],
        experiment_name=experiment_name,
    )


if __name__ == "__main__":
    main()

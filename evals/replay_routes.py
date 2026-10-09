"""Replay the outer loop's routing over existing experimental results.

    python -m evals.replay_routes
    make routes

No model calls. Loads out/*__t*.json, regrades with the current rule, applies
route() to each exit reason as attempt 1, and reports what the outer loop
would have done with each arm's outputs.

Writes out/routes.png.
"""

from __future__ import annotations

import json
import pathlib
import statistics
from collections import defaultdict

from evals.metrics import regrade
from evals.report import load_all, spread
from outer.route import Route, route

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

# Display labels for route categories (stack order preserved below).
ROUTE_LABELS = {
    "shipped_correct":  "shipped correct",
    "shipped_wrong":    "shipped wrong",
    "to_permissions":   "to permissions queue",
    "to_review":        "to review queue",
    "retry_pending":    "retry pending",
}

ROUTE_COLORS = {
    "shipped_correct":  "#3AA08F",
    "shipped_wrong":    "#C4443C",
    "to_permissions":   "#D98518",
    "to_review":        "#8A6FB0",
    "retry_pending":    "#7A8699",
}

# Preferred arm order: naive first, engineered last, others in between.
ARM_ORDER = [
    "naive",
    "no_invariants",
    "answer_free",
    "no_feedback",
    "no_progress_check",
    "no_blocker_check",
    "engineered",
]

ARM_LABELS = {
    "naive":             "naive",
    "engineered":        "engineered",
    "answer_free":       "answer-free\ninvariants",
    "no_invariants":     "no invariants",
    "no_feedback":       "no error\nclassification",
    "no_progress_check": "no progress check",
    "no_blocker_check":  "no blocker check",
}

# Unanswerable question ids for behaviour-tier detail section.
UNANSWERABLE_IDS = {"q33", "q34"}


def _classify_score(s: dict) -> str:
    """Return the route category key for a single regraded score."""
    r = route(s["exit_reason"], attempt=1)
    if r is Route.ACCEPT:
        return "shipped_correct" if s["correct"] else "shipped_wrong"
    if r is Route.HUMAN_PERMISSIONS:
        return "to_permissions"
    if r is Route.HUMAN_REVIEW:
        return "to_review"
    # Route.RETRY
    return "retry_pending"


def _trial_counts(trial_scores: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {k: 0 for k in ROUTE_LABELS}
    for s in trial_scores:
        counts[_classify_score(s)] += 1
    return counts


def _search_attempts(attempts, *needles: str) -> list[str]:
    """Return which needles appear anywhere in the serialized attempt list."""
    blob = json.dumps(attempts)
    return [n for n in needles if n in blob]


def report(byarm: dict) -> None:
    """Print per-arm route distribution tables and behaviour-tier details."""
    # Ordered list of arms present in data.
    present = [a for a in ARM_ORDER if a in byarm] + [
        a for a in byarm if a not in ARM_ORDER
    ]

    print("\n  Outer loop routing replay (attempt 1 for every run, out of 34 tasks)")
    print("  Routes: ACCEPT -> shipped; HUMAN_PERMISSIONS -> permissions queue;")
    print("  HUMAN_REVIEW -> review queue; RETRY -> retry pending (would re-run).")
    print()

    w = 18
    head = "".join(f"{ARM_LABELS.get(a, a).replace(chr(10), ' '):>{w}}" for a in present)
    rule = "=" * (26 + w * len(present))
    print(f"  {'':26}{head}")
    trials_row = "".join(f"{len(byarm[a]):>{w}}" for a in present)
    print(f"  {'trials':26}{trials_row}")
    print(rule)

    # Per-arm trial-level counts -> means with spread.
    arm_trial_counts: dict[str, list[dict]] = {
        a: [_trial_counts(t) for t in byarm[a]] for a in present
    }

    for key, label in ROUTE_LABELS.items():
        cells = "".join(
            f"{spread([c[key] for c in arm_trial_counts[a]]):>{w}}"
            for a in present
        )
        print(f"  {label:26}{cells}")

    print(rule)

    # Cross-check: shipped_wrong == false_success_count per trial.
    print()
    n_violations = 0
    for arm in present:
        for i, trial_scores in enumerate(byarm[arm], 1):
            false_success_count = sum(s["false_success"] for s in trial_scores)
            shipped_wrong_count = arm_trial_counts[arm][i - 1]["shipped_wrong"]
            if shipped_wrong_count != false_success_count:
                n_violations += 1
                print(f"  FAIL cross-check {arm} trial {i}: "
                      f"shipped_wrong={shipped_wrong_count} "
                      f"false_success={false_success_count}")
    if n_violations:
        raise SystemExit(f"\n  {n_violations} cross-check violation(s). "
                         "route() ACCEPT set does not match CLAIMED_SUCCESS.")

    # Behaviour-tier detail for unanswerable questions (q33, q34).
    print("\n  Unanswerable question detail (q33, q34)")
    print(f"  {'arm':<20} {'trial':>5} {'qid':>4}  "
          f"{'exit_reason':<18} {'route':<20} payments  denied")
    print("  " + "-" * 80)

    arm_unans_summary: dict[str, dict] = {}
    for arm in present:
        to_perms = 0
        to_perms_touched_payments = 0
        for trial_idx, trial_scores in enumerate(byarm[arm], 1):
            for s in trial_scores:
                if s.get("question_id") not in UNANSWERABLE_IDS:
                    continue
                r = route(s["exit_reason"], attempt=1)
                hits = _search_attempts(
                    s.get("attempts", []),
                    "payments", "PERMISSION", "not granted",
                )
                touched = "payments" in hits
                denied = any(x in hits for x in ("PERMISSION", "not granted"))
                print(f"  {arm:<20} {trial_idx:>5} {s['question_id']:>4}  "
                      f"{s['exit_reason']:<18} {r.value:<20} "
                      f"{'yes':>8}  {'yes' if denied else 'no':>6}"
                      if touched else
                      f"  {arm:<20} {trial_idx:>5} {s['question_id']:>4}  "
                      f"{s['exit_reason']:<18} {r.value:<20} "
                      f"{'no':>8}  {'yes' if denied else 'no':>6}")
                if r is Route.HUMAN_PERMISSIONS:
                    to_perms += 1
                    if touched:
                        to_perms_touched_payments += 1
        arm_unans_summary[arm] = {
            "to_perms": to_perms,
            "to_perms_payments": to_perms_touched_payments,
        }

    print()
    print("  Per-arm unanswerable summary")
    print(f"  {'arm':<20}  to permissions queue  of those: touched payments")
    print("  " + "-" * 60)
    for arm in present:
        s = arm_unans_summary[arm]
        print(f"  {arm:<20}  {s['to_perms']:>20}  {s['to_perms_payments']:>22}")


def chart_routes(byarm: dict) -> None:
    """Stacked bar chart: one bar per arm, segmented by route category.

    Style matches chart_rates in evals/report.py: same figsize, dpi, spine
    treatment, font sizes, and y-axis grid.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    present = [a for a in ARM_ORDER if a in byarm] + [
        a for a in byarm if a not in ARM_ORDER
    ]
    labels = [ARM_LABELS.get(a, a) for a in present]

    # Mean counts per category across trials.
    def mean_counts(arm: str, key: str) -> float:
        return statistics.mean(
            _trial_counts(t)[key] for t in byarm[arm]
        )

    keys = list(ROUTE_LABELS)
    x = range(len(present))

    fig, ax = plt.subplots(figsize=(13.5, 5.6))
    bottoms = [0.0] * len(present)

    for key in keys:
        vals = [mean_counts(arm, key) for arm in present]
        bars = ax.bar(list(x), vals, bottom=bottoms, label=ROUTE_LABELS[key],
                      color=ROUTE_COLORS[key])
        for i, (v, b) in enumerate(zip(vals, bottoms)):
            if v >= 0.5:
                ax.text(i, b + v / 2, f"{v:.0f}", ha="center", va="center",
                        fontsize=11, color="#ffffff", fontweight="bold")
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=13)
    ax.set_ylabel("mean tasks per trial (34 total)", fontsize=12)
    ax.set_ylim(0, max(bottoms) * 1.12 or 36)
    ax.tick_params(axis="x", length=0, pad=10)
    ax.tick_params(axis="y", labelsize=11, colors="#5a6167")
    ax.set_title("Outer loop routing by arm (attempt 1)", fontsize=16, pad=18, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines["bottom"].set_color("#cfd3d7")
    ax.spines["left"].set_color("#cfd3d7")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    handles, lab = ax.get_legend_handles_labels()
    ax.legend(handles, lab, loc="upper center",
              bbox_to_anchor=(0.5, -0.16), ncol=len(keys), frameon=False, fontsize=12)
    fig.tight_layout()
    out_path = OUT / "routes.png"
    fig.savefig(out_path, dpi=200)
    print(f"\n  wrote {out_path}")


def main() -> None:
    byarm = load_all()
    report(byarm)
    chart_routes(byarm)
    print()


if __name__ == "__main__":
    main()

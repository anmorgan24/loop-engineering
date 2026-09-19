"""Aggregate every arm and trial in out/ into the table and the chart.

    python -m evals.report

Reads out/<arm>__t<n>.json. Reports the mean across trials with the spread, so
a gap that is inside the noise is visible as such rather than presented as a
result.

Writes out/exit_reasons.png (the before/after slide) and, when more than one
engineered arm is present, out/ablation.png.
"""

from __future__ import annotations

import json
import pathlib
import statistics
from collections import defaultdict

from evals.metrics import summarise

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

EXIT_ORDER = ["model_said_done", "budget_exhausted", "no_progress",
              "escalate", "hard_blocker", "verified_success"]

LABELS = {"model_said_done": "model said done", "budget_exhausted": "budget exhausted",
          "no_progress": "no progress", "escalate": "escalated",
          "hard_blocker": "hard blocker", "verified_success": "verified success"}

ARM_LABELS = {"naive": "naive", "engineered": "engineered",
              "no_invariants": "no rung 5", "no_feedback": "raw error text",
              "no_progress_check": "no progress check",
              "no_blocker_check": "no blocker check"}


def load_all() -> dict:
    """arm -> list of per-trial score lists."""
    byarm = defaultdict(list)
    for p in sorted(OUT.glob("*__t*.json")):
        d = json.loads(p.read_text())
        byarm[d["arm"]].append(d["scores"])
    if not byarm:
        raise SystemExit(
            "no results in out/. Run: python -m evals.run_corpus --arm naive engineered")
    return dict(byarm)


def spread(vals: list[float], dp: int = 1) -> str:
    if len(vals) < 2:
        return f"{vals[0]:.{dp}f}"
    return (f"{statistics.mean(vals):.{dp}f} "
            f"+/-{(max(vals) - min(vals)) / 2:.{dp}f}")


def main() -> None:
    byarm = load_all()
    order = [a for a in ["naive", "engineered", "no_invariants", "no_feedback",
                         "no_progress_check", "no_blocker_check"] if a in byarm]
    reps = {a: [summarise(a, t) for t in byarm[a]] for a in order}
    ntrials = {a: len(byarm[a]) for a in order}

    w = 17
    head = "".join(f"{ARM_LABELS.get(a, a):>{w}}" for a in order)
    print("\n" + "=" * (28 + w * len(order)))
    print(f"  {'':<26}{head}")
    print(f"  {'trials':<26}" + "".join(f"{ntrials[a]:>{w}}" for a in order))
    print("=" * (28 + w * len(order)))

    def line(label, fn, suffix="", dp=1, prefix=""):
        cells = "".join(
            f"{prefix + spread([fn(r) for r in reps[a]], dp) + suffix:>{w}}"
            for a in order)
        print(f"  {label:<26}{cells}")

    line("solve rate", lambda r: r.solve_rate, "%")
    line("false success rate", lambda r: r.false_success_rate, "%")
    line("iters to solve p50", lambda r: r.iters_p50)
    line("iters to solve p95", lambda r: r.iters_p95)
    line("cost per solved", lambda r: r.usd_per_solved, dp=3, prefix="$")
    line("cost on failed runs", lambda r: r.usd_wasted, dp=3, prefix="$")
    line("redundant action rate", lambda r: r.redundant_action_rate, "%")
    print("=" * (28 + w * len(order)))

    print("\n  exit reasons (mean per trial)")
    for k in EXIT_ORDER:
        vals = {a: statistics.mean([r.exit_reasons.get(k, 0) for r in reps[a]])
                for a in order}
        if any(vals.values()):
            print(f"  {LABELS[k]:<26}" + "".join(f"{vals[a]:>{w}.1f}" for a in order))

    print("\n  blocked tasks handled")
    print(f"  {'':<26}" + "".join(f"{reps[a][0].blocked_handled:>{w}}" for a in order))

    chart_exits(reps, order)
    if len([a for a in order if a not in ("naive",)]) > 1:
        chart_ablation(reps, order)
    print()


def _mean_exits(rs, k):
    return statistics.mean([r.exit_reasons.get(k, 0) for r in rs])


def chart_exits(reps, order) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pair = [a for a in ("naive", "engineered") if a in order] or order[:2]
    keys = [k for k in EXIT_ORDER if any(_mean_exits(reps[a], k) for a in pair)]
    x = range(len(keys))
    w = 0.38
    colors = {"naive": "#B0B7C3", "engineered": "#2F6FEB"}

    fig, ax = plt.subplots(figsize=(11, 5.2))
    for i, a in enumerate(pair):
        vals = [_mean_exits(reps[a], k) for k in keys]
        off = (i - (len(pair) - 1) / 2) * w
        ax.bar([j + off for j in x], vals, w, label=ARM_LABELS.get(a, a),
               color=colors.get(a, "#7A8699"))
        for j, v in enumerate(vals):
            if v:
                ax.text(j + off, v + 0.15, f"{v:.0f}", ha="center", fontsize=11)

    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS[k] for k in keys], fontsize=12)
    ax.set_ylabel("tasks", fontsize=12)
    ax.set_title("Why the loop stopped", fontsize=16, pad=14, loc="left")
    ax.legend(frameon=False, fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    fig.tight_layout()
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / "exit_reasons.png", dpi=200)
    print(f"\n  wrote {OUT / 'exit_reasons.png'}")


def chart_ablation(reps, order) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [ARM_LABELS.get(a, a) for a in order]
    solve = [statistics.mean([r.solve_rate for r in reps[a]]) for a in order]
    false = [statistics.mean([r.false_success_rate for r in reps[a]]) for a in order]
    x = range(len(order))
    w = 0.38

    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.bar([i - w / 2 for i in x], solve, w, label="solve rate", color="#2F6FEB")
    ax.bar([i + w / 2 for i in x], false, w, label="false success rate", color="#E4572E")
    for i, (s, f) in enumerate(zip(solve, false)):
        ax.text(i - w / 2, s + 1, f"{s:.0f}", ha="center", fontsize=11)
        ax.text(i + w / 2, f + 1, f"{f:.0f}", ha="center", fontsize=11)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel("percent of tasks", fontsize=12)
    ax.set_ylim(0, 105)
    ax.set_title("What each part of the loop is worth", fontsize=16, pad=14, loc="left")
    ax.legend(frameon=False, fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "ablation.png", dpi=200)
    print(f"  wrote {OUT / 'ablation.png'}")


if __name__ == "__main__":
    main()

"""Aggregate every arm and trial in out/ into the table and the chart.

    python -m evals.report

Reads out/<arm>__t<n>.json. Reports the mean across trials with the spread, so
a gap that is inside the noise is visible as such rather than presented as a
result.

Three comparisons, kept apart because they answer different questions:

    naive vs engineered    everything at once. Shows the size of the gap, not
                           which part of the loop produced it.
    verification ladder    no invariants -> answer-free -> calibrated. The one
                           place the arms form a real sequence, each adding
                           one thing to the one before.
    knockouts              each arm removes one feature from `engineered`.
                           Read each against `engineered` only, never against
                           each other.

Writes out/exit_reasons.png, out/ladder.png and out/knockouts.png.
"""

from __future__ import annotations

import json
import pathlib
import statistics
from collections import defaultdict

from agent.config import KNOCKOUT_ARMS, VERIFICATION_ARMS
from evals.metrics import (BEHAVIOUR_TIERS, behaviour_correct,
                           outcome_class, regrade, summarise)

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

EXIT_ORDER = ["model_said_done", "budget_exhausted", "no_progress",
              "escalate", "hard_blocker", "verified_success"]

LABELS = {"model_said_done": "model said done", "budget_exhausted": "budget exhausted",
          "no_progress": "no progress", "escalate": "escalated",
          "hard_blocker": "hard blocker", "verified_success": "verified success"}

ARM_LABELS = {"naive": "naive", "engineered": "engineered",
              "answer_free": "answer-free\ninvariants", "no_invariants": "no invariants",
              "no_feedback": "no error\nclassification",
              "no_progress_check": "no progress check",
              "no_blocker_check": "no blocker check"}


def load_all() -> dict:
    """arm -> list of per-trial score lists. Ignores --limit probe runs."""
    byarm = defaultdict(list)
    for p in sorted(OUT.glob("*__t*.json")):
        if "__limit" in p.name:
            continue
        d = json.loads(p.read_text())
        byarm[d["arm"]].append([_regrade(s) for s in d["scores"]])
    if not byarm:
        raise SystemExit(
            "no results in out/. Run: python -m evals.run_corpus --arm naive engineered")

    # Arms with different task counts are not comparable. Say so loudly
    # rather than printing a table that looks fine.
    sizes = {a: sorted({len(t) for t in trials}) for a, trials in byarm.items()}
    if len({n for ns in sizes.values() for n in ns}) > 1:
        print("\n  WARNING: arms have different task counts and are NOT comparable")
        for a, ns in sizes.items():
            print(f"    {a:<20} {ns}")
        print("    delete the short files in out/ and rerun those arms\n")
    return dict(byarm)


def _regrade(s: dict) -> dict:
    return regrade(s)


def spread(vals: list[float], dp: int = 1) -> str:
    if len(vals) < 2:
        return f"{vals[0]:.{dp}f}"
    return (f"{statistics.mean(vals):.{dp}f} "
            f"+/-{(max(vals) - min(vals)) / 2:.{dp}f}")


def table(title: str, arms: list, reps: dict, ntrials: dict, note: str = "") -> None:
    arms = [a for a in arms if a in reps]
    if len(arms) < 2:
        print(f"\n  {title}: needs {', '.join(arms) or 'these arms'} plus at least one "
              "more. Skipped.")
        return
    names = [ARM_LABELS.get(a, a).replace("\n", " ") for a in arms]
    w = max(17, max(len(n) for n in names) + 2)
    head = "".join(f"{n:>{w}}" for n in names)
    rule = "=" * (28 + w * len(arms))
    print(f"\n  {title}")
    if note:
        print(f"  {note}")
    print(rule)
    print(f"  {'':<26}{head}")
    print(f"  {'trials':<26}" + "".join(f"{ntrials[a]:>{w}}" for a in arms))
    print(rule)

    def line(label, fn, suffix="", dp=1, prefix=""):
        cells = "".join(
            f"{prefix + spread([fn(r) for r in reps[a]], dp) + suffix:>{w}}"
            for a in arms)
        print(f"  {label:<26}{cells}")

    line("solve (all tiers)", lambda r: r.solve_rate, "%")
    line("false success (all)", lambda r: r.false_success_rate, "%")
    line("stopped unsolved (all)", lambda r: r.stopped_unsolved_rate, "%")
    line("solve (answerable)", lambda r: r.answerable_solve_rate, "%")
    line("false success (answerable)", lambda r: r.answerable_false_success, "%")
    line("iters to solve p50", lambda r: r.iters_p50)
    line("iters to solve p95", lambda r: r.iters_p95)
    line("cost per solved", lambda r: r.usd_per_solved, dp=3, prefix="$")
    line("cost on failed runs", lambda r: r.usd_wasted, dp=3, prefix="$")
    line("redundant action rate", lambda r: r.redundant_action_rate, "%")
    print(rule)

    print("  exit reasons (mean per trial)")
    for k in EXIT_ORDER:
        vals = {a: statistics.mean([r.exit_reasons.get(k, 0) for r in reps[a]])
                for a in arms}
        if any(vals.values()):
            print(f"  {LABELS[k]:<26}" + "".join(f"{vals[a]:>{w}.1f}" for a in arms))

    def tier_line(label, attr):
        cells = "".join(
            (" ".join(getattr(r, attr) for r in reps[a])).rjust(w) for a in arms)
        print(f"  {label:<26}{cells}")

    tier_line("blocked: hard_blocker", "blocked_handled")
    tier_line("unanswerable: noticed", "unanswerable_detected")


def main() -> None:
    byarm = load_all()
    reps = {a: [summarise(a, t) for t in trials] for a, trials in byarm.items()}
    ntrials = {a: len(t) for a, t in byarm.items()}

    print("\n  solve + false success + stopped unsolved = 100% of runs.")
    print("  Blocked and unanswerable tasks count as solved when the loop stops on")
    print("  hard_blocker, no_progress or escalate. Budget exhaustion does not count:")
    print("  that is the fuse noticing, not the loop.")

    table("1. naive vs engineered", ["naive", "engineered"], reps, ntrials,
          "Every design choice at once. The size of the gap, not its source.")
    table("2. verification ladder", VERIFICATION_ARMS, reps, ntrials,
          "Each column adds one thing to the column before it.")
    table("3. single knockouts", KNOCKOUT_ARMS + ["engineered"], reps, ntrials,
          "Each column removes one feature from engineered. Compare each to "
          "engineered only.")

    from evals.questions import ANSWERABLE
    cal = [q.id for q in ANSWERABLE if q.invariants.value_band is not None]
    print(f"\n  {len(cal)}/{len(ANSWERABLE)} answerable questions carry a calibrated "
          f"magnitude band:\n    {', '.join(cal)}")
    print("  Those bands were written with the answers in hand. The answer-free "
          "arm\n  drops them, and the gap is the part of the score that needed "
          "prior knowledge.")

    chart_exits(reps, list(reps))
    chart_rates(reps, [a for a in VERIFICATION_ARMS if a in reps], "ladder.png",
                "The verification ladder: each bar adds one thing to the one before")
    chart_rates(reps, [a for a in KNOCKOUT_ARMS if a in reps] + (
                ["engineered"] if "engineered" in reps else []), "knockouts.png",
                "Single knockouts: each bar removes one feature from engineered",
                reference="engineered")
    print()


def _mean_exits(rs, k):
    return statistics.mean([r.exit_reasons.get(k, 0) for r in rs])


def chart_exits(reps, order) -> None:
    """One bar per arm, segmented by exit reason.

    Not a grouped chart. Grouping implies the arms share categories, and they
    do not: naive only ever exits one way, so every group had one real bar and
    one empty slot sitting off-centre from its tick.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pair = [a for a in ("naive", "engineered") if a in order] or order[:2]
    totals = {a: {k: sum(r.exit_reasons.get(k, 0) for r in reps[a])
                  for k in EXIT_ORDER} for a in pair}
    keys = [k for k in EXIT_ORDER if any(totals[a][k] for a in pair)]
    colors = {"model_said_done": "#9AA1A8", "verified_success": "#3AA08F",
              "hard_blocker": "#D98518", "no_progress": "#7A8699",
              "budget_exhausted": "#C4443C", "escalate": "#8A6FB0"}

    arms = [ARM_LABELS.get(a, a) for a in pair]
    fig, ax = plt.subplots(figsize=(13.5, 4.2))
    left = [0] * len(pair)
    for k in keys:
        vals = [totals[a][k] for a in pair]
        ax.barh(arms, vals, left=left, height=0.52,
                color=colors.get(k, "#7A8699"),
                label=LABELS.get(k, k.replace("_", " ")))
        for i, v in enumerate(vals):
            if v:
                ax.text(left[i] + v / 2, i, str(v), ha="center", va="center",
                        fontsize=13, color="#ffffff", fontweight="bold")
        left = [l + v for l, v in zip(left, vals)]

    n = max(left) or 1
    ax.set_xlim(0, n)
    ax.set_xlabel("runs (%d per arm)" % n, fontsize=12)
    ax.tick_params(axis="y", labelsize=15, length=0, pad=10)
    ax.invert_yaxis()
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.set_title("Why the loop stopped", fontsize=17, loc="left", pad=16)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22),
              ncol=len(keys), frameon=False, fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "exit_reasons.png", dpi=200)

def chart_rates(reps, arms, filename, title, reference=None) -> None:
    """Solve and false success per arm.

    Used for the ladder, where left to right is a real sequence, and for the
    knockouts, where it is not. For the knockouts the reference arm is drawn
    last and set off by a divider, so no one reads the bars as a progression.
    """
    if len(arms) < 2:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [ARM_LABELS.get(a, a) for a in arms]
    solve = [statistics.mean([r.solve_rate for r in reps[a]]) for a in arms]
    false = [statistics.mean([r.false_success_rate for r in reps[a]]) for a in arms]
    x = range(len(arms))
    w = 0.38

    # Teal is the same "a check passed" green as the exit reason chart. False
    # success gets its own red rather than that chart's amber: amber there
    # means hard_blocker, which is the loop behaving correctly, and the same
    # colour meaning the opposite thing two slides later is worse than two
    # unmatched palettes.
    fig, ax = plt.subplots(figsize=(13.5, 5.6))
    ax.bar([i - w / 2 for i in x], solve, w, label="solve rate", color="#3AA08F")
    ax.bar([i + w / 2 for i in x], false, w, label="false success rate", color="#C4443C")
    for i, (s_, f) in enumerate(zip(solve, false)):
        ax.text(i - w / 2, s_ + 1.5, f"{s_:.0f}", ha="center", fontsize=13,
                color="#16191d", fontweight="bold")
        ax.text(i + w / 2, f + 1.5, f"{f:.0f}", ha="center", fontsize=13,
                color="#16191d", fontweight="bold")
    if reference in arms:
        ax.axvline(arms.index(reference) - 0.5, color="#9AA1A8", ls="--", lw=1)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=13)
    ax.set_ylabel("percent of all 34 tasks", fontsize=12)
    ax.set_ylim(0, 105)
    ax.tick_params(axis="x", length=0, pad=10)
    ax.tick_params(axis="y", labelsize=11, colors="#5a6167")
    ax.set_title(title, fontsize=16, pad=18, loc="left")
    handles, lab = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], lab[::-1], loc="upper center",
              bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False, fontsize=13)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines["bottom"].set_color("#cfd3d7")
    ax.spines["left"].set_color("#cfd3d7")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / filename, dpi=200)
    print(f"  wrote {OUT / filename}")


if __name__ == "__main__":
    main()

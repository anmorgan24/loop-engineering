"""Turn the two corpora into the table and the chart.

    python -m evals.report

Writes out/exit_reasons.png, which is the slide. Everything else prints.
"""

from __future__ import annotations

import json
import pathlib

from evals.metrics import summarise

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

# Ordered worst-to-best so the chart reads left to right as an improvement.
EXIT_ORDER = [
    "model_said_done",
    "budget_exhausted",
    "no_progress",
    "escalate",
    "hard_blocker",
    "verified_success",
]

LABELS = {
    "model_said_done": "model said done",
    "budget_exhausted": "budget exhausted",
    "no_progress": "no progress",
    "escalate": "escalated",
    "hard_blocker": "hard blocker",
    "verified_success": "verified success",
}


def load(name: str):
    p = OUT / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"missing {p}. Run: python -m evals.run_corpus --loop {name}")
    return json.loads(p.read_text())["scores"]


def row(label: str, a, b, fmt="{}") -> str:
    return f"  {label:<26} {fmt.format(a):>16} {fmt.format(b):>16}"


def main() -> None:
    naive = summarise("naive", load("naive"))
    eng = summarise("engineered", load("engineered"))

    print("\n" + "=" * 62)
    print(f"  {'':<26} {'naive':>16} {'engineered':>16}")
    print("=" * 62)
    print(row("tasks", naive.n, eng.n))
    print(row("solve rate", naive.solve_rate, eng.solve_rate, "{}%"))
    print(row("false success rate", naive.false_success_rate, eng.false_success_rate, "{}%"))
    print(row("iterations to solve p50", naive.iters_p50, eng.iters_p50))
    print(row("iterations to solve p95", naive.iters_p95, eng.iters_p95))
    print(row("total cost", naive.usd_total, eng.usd_total, "${}"))
    print(row("cost per solved task", naive.usd_per_solved, eng.usd_per_solved, "${}"))
    print(row("cost on failed runs", naive.usd_wasted, eng.usd_wasted, "${}"))
    print(row("redundant action rate", naive.redundant_action_rate,
              eng.redundant_action_rate, "{}%"))
    print(row("blocked tasks handled", naive.blocked_handled, eng.blocked_handled))
    print("=" * 62)

    print("\n  exit reasons")
    for k in EXIT_ORDER:
        a, b = naive.exit_reasons.get(k, 0), eng.exit_reasons.get(k, 0)
        if a or b:
            print(f"  {LABELS[k]:<26} {a:>16} {b:>16}")

    print("\n  recovery rate by error class (naive / engineered)")
    keys = sorted(set(naive.recovery_by_class) | set(eng.recovery_by_class))
    for k in keys:
        a = naive.recovery_by_class.get(k, 0.0)
        b = eng.recovery_by_class.get(k, 0.0)
        print(f"  {k:<26} {a:>15}% {b:>15}%")

    chart(naive, eng)
    print(f"\n  wrote {OUT / 'exit_reasons.png'}\n")


def chart(naive, eng) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    keys = [k for k in EXIT_ORDER
            if naive.exit_reasons.get(k) or eng.exit_reasons.get(k)]
    a = [naive.exit_reasons.get(k, 0) for k in keys]
    b = [eng.exit_reasons.get(k, 0) for k in keys]
    x = range(len(keys))
    w = 0.38

    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.bar([i - w / 2 for i in x], a, w, label="naive loop", color="#B0B7C3")
    ax.bar([i + w / 2 for i in x], b, w, label="engineered loop", color="#2F6FEB")

    for i, (av, bv) in enumerate(zip(a, b)):
        if av:
            ax.text(i - w / 2, av + 0.15, str(av), ha="center", fontsize=11)
        if bv:
            ax.text(i + w / 2, bv + 0.15, str(bv), ha="center", fontsize=11)

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


if __name__ == "__main__":
    main()

# loop-engineering

A text-to-SQL agent over DuckDB, built twice (naive loop vs engineered loop),
plus an eval harness that measures what each loop design choice is worth.
Companion code for a conference talk and a technical article. The numbers in
README.md get quoted publicly, so measurement correctness beats speed.

## Environment

- Python 3.12 via uv (`uv venv && source .venv/bin/activate && uv pip install -r requirements.txt`).
  The system python3 on this Mac resolves to 3.9 and fails confusingly.
- `make seed` builds the database. `make smoke` and `make audit` need no key or network.

## Commands

- `make smoke`: scripted model, every exit reason, ablation flags, unit checks. Run after any change to agent/ or evals/.
- `make audit`: checks questions for LIMIT ties, unpinned output shapes, weak invariants. Run after any edit to evals/questions.py.
- `make report`: three tables plus out/exit_reasons.png, out/ladder.png, out/knockouts.png.
- `make cost`, `make pair`, `make core`: real model calls that cost money. Never run these yourself. Tell me the command and I will run it.

## Architecture rules

- `should_continue` in agent/gate.py is the only thing that ends the engineered loop. Nothing else in loop.py returns. This is what makes `exit_reason` trustworthy.
- `agent/` never imports `evals/`. Rungs 1 to 5 (agent/verify.py) gate the loop. Rung 6 and any LLM judge (evals/metrics.py) measure offline and decide nothing.
- `llm.propose_action` is the only non-deterministic step. smoke.py swaps it out; keep that seam clean.
- `OPIK_PROJECT_NAME` is set in agent/llm.py immediately before `import opik`, because the SDK reads it once at import.

## Measurement rules

- Fix the data, not the analysis. If a result looks wrong, suspect the question, the grader, or the harness before the agent. Several past "agent failures" were harness bugs.
- Each non-naive arm removes exactly one feature from `engineered`. Compare knockouts to `engineered` only. Only no_invariants, answer_free, engineered form a real sequence.
- Any change to the gate, the fingerprint, the verifier, or the error classifier changes which runs exit how. Existing files in out/ are then stale for every arm: say so and do not mix old and new results.
- Calibrated invariants (`value_band`, the only one) may not be derived from gold answers for new questions without saying so. Answer-free invariants come from the question and schema alone.
- Blocked and unanswerable tiers are graded from `exit_reason` via `behaviour_correct` in evals/metrics.py. Budget exhaustion is not "noticing". The Opik metric and the report must share that rule.
- Outcomes are solved, false_success, stopped_unsolved and always sum to 100.
- out/ is gitignored and holds results that took real money to produce. Never delete or rewrite files there.

## Writing style (README, docstrings, comments)

Plain, technically precise sentences. No em or en dashes. No hype adjectives or superlatives. Do not claim more than the data shows: state trial counts and noise where a gap is small.

## Open work

- Fill the two TODO rows in README.md (no_progress_check, no_blocker_check) from `make report` output.
- Opik experiment script: `ResultSetMatch` in evals/metrics.py is defined but never called. Needs a dataset built from evals/questions.py and an `opik.evaluate` run.
- Deferred: alias-insensitive fingerprinting in `canonical_hash`. Needs a full rerun of every arm.

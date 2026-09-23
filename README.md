# loop-engineering

A text-to-SQL agent built twice: once the way most agent loops are written, and
once with the loop engineered. Same model, same tools, same prompt, same
schema. The only difference is the loop.

Companion code for a talk on loop engineering.

## Why text-to-SQL

Because verification looks easy here and is not. A query that runs is not a
query that is right, and every interesting failure in this schema, discounts
ignored, cancelled orders counted, customers dropped by an inner join, parses,
plans, and executes cleanly. That gap is the argument.

Coding agents make the point too easily. `pytest` already exists, so the
verifier question never comes up. Here you have to build it.

## What the runs showed

Five arms, three trials, 34 tasks each. 510 runs.

| arm | solve | false success |
|---|---|---|
| naive | 77% | 23% |
| no invariants | 85% | 15% |
| answer-free invariants | 88% | 12% |
| no error classification | 88% | 9% |
| engineered | 93% | 7% |

False success is the number that matters: the loop reported done and was
wrong. Invariants are the largest single lever, and the half of them you could
write without knowing the answer recover under half the gap. The rest came
from calibrated bands, which is a caveat rather than a result. See below.

Across all 510 runs the iteration budget was never exhausted in any arm. The
other exits caught everything first.

## Setup

Needs Python 3.10 or newer. The system Python on macOS is usually older, and
`pip install` will fail with a wall of version errors rather than saying so.

With [uv](https://github.com/astral-sh/uv), which handles the interpreter for
you:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Or with stdlib venv, substituting a 3.10+ interpreter:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then:

```bash
make seed
```

`make smoke` works from here with no API key. For anything that calls a model:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Optional, for traces and experiments:

```bash
export OPIK_API_KEY=...
export OPIK_WORKSPACE=...
```

Traces land in a project called `loop-engineering`. Override with
`OPIK_PROJECT_NAME`, which must be set before the process starts, since the
SDK reads it at import time.

## Run

```bash
make help         # list every target
make audit        # check the questions before trusting any number
make smoke        # full loop + ablation test, no API key needed
make cost         # 3 tasks, one arm, to measure spend before committing
make pair         # naive vs engineered, 3 trials
make core         # naive + engineered + 3 ablations, 3 trials each
make report       # the table, out/exit_reasons.png, out/ablation.png
```

`make smoke` replaces the model with a scripted stand-in and drives every exit
reason. It needs no network and no key, so it works as a live demo when the
venue wifi does not.

`make core` is 510 model-driven runs and takes about 45 minutes. Run `make
cost` first.

Results land in `out/<arm>__t<n>.json`, one file per arm per trial, so a
crashed trial costs you that trial and nothing else. Reruns skip files that
already exist.

## Map

| file | what it is |
|---|---|
| `agent/loop.py` | the engineered loop. `run` is the slide |
| `agent/naive.py` | the before loop. Two exit conditions, both weak |
| `agent/gate.py` | budget, state, and `should_continue`, the only thing that ends a loop |
| `agent/verify.py` | the five-rung verifier ladder |
| `agent/classify.py` | the error taxonomy and what gets fed back |
| `agent/tools.py` | what the agent can call, and which tables it may not read |
| `agent/db.py` | schema, seed, and a list of the traps with the rung that catches each |
| `evals/questions.py` | 34 tasks with gold SQL and per-question invariants |
| `evals/metrics.py` | rung 6 and the corpus metrics. Never imported by the loop |
| `evals/report.py` | the before/after table and the charts |

## Answer-free vs calibrated invariants

Invariants split by what they need to exist.

**Answer-free** ones come from the question and the schema alone: column
counts, a row count the question states outright, and reconciliation against a
second, independently written query. You can write them before anyone computes
the answer, which is the situation the loop is actually in at runtime.

**Calibrated** ones need prior knowledge: a plausible magnitude for revenue,
a sane range for a count. Legitimate in production, where last month's number
exists. Not legitimate when derived from the answer key of the suite you are
scoring.

The `answer_free` arm drops the calibrated set, and the gap between 88% and
93% is what the calibrated bands are worth. Reporting both is the honest
version of a verification claim, and `make report` prints which questions still
lean on a band.

## The two ladders

Runtime verification stops at rung 5. Comparing against a gold answer is rung 6
and lives in `evals/`, because at runtime you do not have the answer. Keeping
them in separate modules is not tidiness, it is the control plane and the
measurement plane:

```
control plane      rungs 1-5, deterministic, decides whether the loop continues
measurement plane  rung 6 and the LLM judge, offline, decides nothing
```

An LLM judge is a fine metric and a terrible gate. `evals/metrics.py` may not be
imported from `agent/`, and is not.

## Exit reasons

```
verified_success    a deterministic check passed
budget_exhausted    iterations, tokens, wall clock, or dollars
no_progress         same query fingerprint, or stuck at the same rung
hard_blocker        nothing the agent can emit will work
escalate            repeated malformed actions
model_said_done     naive loop only, and the reason it is naive
```

Logging the exit reason on every trace is the single highest-signal change in
this repo. Its distribution over a corpus tells you what kind of loop problem
you have before you open a single trace.

## Audit the questions first

`make audit` checks every question for three things that silently corrupt
results: a LIMIT that cuts a tie, an output shape the question never pins down,
and invariants too weak to reject a wrong answer. The first draft of the suite
had six such bugs. They did not look like bugs, they looked like the agent
failing, and they would have gone on a slide.

Run it after adding or editing any question.

## Three tiers, three questions being asked

The suite mixes questions that have a right answer with questions that do not,
and blending them into one score measures two different things at once. 34
tasks: 30 answerable, and 4 that grade a behaviour instead.

- **answerable** (easy, medium, trap, hard): graded against a gold result set,
  order-insensitive, numbers rounded to 2dp and strings case-folded. This is
  the solve rate.
- **blocked**: needs a table the agent may not read. Graded on whether the loop
  stopped rather than kept rewriting. The agent is told, via an error.
- **unanswerable**: the data does not exist anywhere in the schema and nothing
  is denied. The agent has to work it out from the catalogue. Detection here is
  unreliable even in the full loop, which is a finding rather than a bug.

`make report` prints all three separately. The headline solve rate is over all
34, so correctly refusing an unanswerable task counts as solved.

## Caveats

- 34 hand-written questions against one schema is enough to show a difference
  in behaviour. It is not a benchmark, and the absolute numbers should not be
  read as a general claim about text-to-SQL.
- Three trials per arm is enough to keep an eight-point gap out of the noise.
  It is not enough for a two-point one. Raise `--trials` if you need tighter
  numbers.
- Part of the engineered arm's margin comes from calibrated invariants, which
  were written with the answer key in hand. The `answer_free` arm is the number
  to quote if you want one that survives production.
- Prices in `agent/llm.py` were checked in September 2026. Re-check them before
  putting a cost figure on a slide.
- The invariants are the interesting part and they are hand written. That is
  the work, and there is no way around it.

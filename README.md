# loop-engineering

I built a text-to-SQL agent twice. One version is written the way most agent
loops are written. The other has an engineered loop. Same model, same tools,
same prompt, same schema.

Companion code for a talk of the same name, given at WeAreDevelopers World
Congress North America in September 2026.

## What the naive loop does

Here is one run against a table the agent is not allowed to read:

```
  question
  Which payment method is used most often?

  [2] describe_table -> error: read access to payments is not granted to this agent
  [3] run_query      -> error: read access to payments is not granted to this agent
  [4] run_query      -> error: read access to payments is not granted to this agent

  stopped after 5 iterations
  exit reason: model_said_done

  sql
  SELECT method, COUNT(*) AS usage_count
  FROM payments
  GROUP BY method
  ORDER BY usage_count DESC
  LIMIT 1;
```

Denied three times, then it submitted a query it had never run and reported
that it was finished. The loop ended because the model said so. Nothing
checked.

The engineered loop sees the same three denials, recognises that no retry will
get past them, and exits with `hard_blocker`. That is the whole argument, and
the rest of this repo is the evidence.

## Why text-to-SQL

SQL looks like an easy place to verify an answer. It is not.

The failures that matter here all produce a query that parses, plans, and runs
cleanly:

- revenue summed from `list_price` instead of `unit_price`, so every discount
  disappears
- cancelled orders counted in the total
- an inner join on a nullable foreign key, quietly dropping every customer
  without a region
- a one-to-many join that multiplies the total by the number of line items

Each one returns a tidy result set full of plausible numbers. Checking that the
query ran tells you nothing at all.

Coding agents are not the exception here. A green test suite says the code ran
and satisfied whatever the tests happen to check. Tests miss cases, and an agent
that writes or edits its own tests can go green without being right. `pytest`
passing is the same rung as the query running. SQL only makes the gap easier to
see, because the wrong answer is a revenue figure somebody recognises.

## What the runs showed

Seven arms, three trials each, 34 tasks. 714 runs. Every run ends in one of
three outcomes, which sum to 100%:

- **solved**: the right answer, or on a blocked or unanswerable task, a stop
  that did not claim success
- **false success**: the loop reported done and was wrong. This is the failure
  you cannot see in production, and the column to read
- **stopped unsolved**: the loop gave up without claiming anything

Each arm other than `naive` is the engineered loop with one thing removed, so
the arms answer three different questions. They are reported separately
because putting them in one ordered row suggests a cumulative build that never
happened.

**1. Naive vs engineered.** Every design choice at once. This shows the size of
the gap, not which part of the loop produced it.

| arm        | solved | false success | stopped unsolved |
| ---------- | ------ | ------------- | ---------------- |
| naive      | 77%    | 23%           | 0%               |
| engineered | 93%    | 7%            | 0%               |

**2. The verification ladder.** The one place the arms form a real sequence:
each row adds one thing to the row above.

| arm                      | solved | false success |
| ------------------------ | ------ | ------------- |
| no invariants (rung 4)   | 85%    | 15%           |
| answer-free invariants   | 88%    | 12%           |
| + calibrated invariants  | 93%    | 7%            |

Adding invariants took solve from 85% to 93%. Three of those eight points came
from invariants you could write without knowing the answer. Five came from
calibrated bands written with the answer key open, which is knowledge you would
not have at runtime. See the section on that below.

**3. Single knockouts.** Each row removes one feature from the engineered loop.
Compare each row to `engineered`, not to the others.

| removed                   | solved | false success | stopped unsolved |
| ------------------------- | ------ | ------------- | ---------------- |
| error classification      | 88%    | 9%            | 3%               |
| no-progress detection     | 91%    | 9%            | 0%               |
| blocker detection         | 87%    | 10%           | 3%               |
| nothing (`engineered`)    | 93%    | 7%            | 0%               |

The false success gap between `no_feedback` and `engineered` is two points,
which three trials cannot separate from noise. The solve gap is five.

Across the five arms in the original run, not one run exhausted its iteration
budget. Every naive run ended because the model said it was done, including
the 23% that were wrong. The failure here was stopping, not spinning.

## Setup

Needs Python 3.10 or newer. The system Python on macOS is usually older, and
`pip install` fails with a wall of version errors instead of telling you why.

With [uv](https://github.com/astral-sh/uv), which picks the interpreter for
you:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
make seed
```

Or with stdlib venv, naming a 3.10+ interpreter yourself:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make seed
```

`make smoke` runs from here with no API key and no network. Anything that calls
a model needs one:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Traces are optional:

```bash
export OPIK_API_KEY=...
export OPIK_WORKSPACE=...
```

They land in a project called `loop-engineering`. Set `OPIK_PROJECT_NAME` to
change that, and set it before the process starts, because the SDK reads it
once at import time.

## Run

```bash
make help         # list every target
make audit        # check the questions before trusting any number
make smoke        # every exit reason, scripted model, no key needed
make cost         # 3 tasks, one arm, to see what spending looks like
make pair         # naive vs engineered, 3 trials
make core         # naive, engineered and 5 knockouts, 3 trials each
make report       # three tables, out/exit_reasons.png, out/ladder.png, out/knockouts.png
```

`make smoke` swaps the model for a scripted stand-in and walks the loop through
every exit reason. No network, no key, about five seconds. It also works as a
live demo when the venue wifi does not.

`make core` is 714 runs and takes roughly an hour. It skips any arm and trial
whose result file already exists, so rerunning it after adding an arm only runs
the new one. Run `make cost` first so the bill is not a surprise.

Results are written to `out/<arm>__t<n>.json`, one file per arm per trial. A
crashed trial costs you that trial and nothing else, and a rerun skips files
that already exist.

## What is where

| file | what it is |
|---|---|
| `agent/loop.py` | the engineered loop |
| `agent/naive.py` | the before loop. Two exit conditions, both weak |
| `agent/gate.py` | `should_continue`, the only function allowed to end a loop |
| `agent/verify.py` | the five-rung verifier ladder |
| `agent/classify.py` | the error taxonomy and what gets fed back to the model |
| `agent/tools.py` | what the agent can call, and which tables it cannot read |
| `agent/db.py` | schema, seed data, and each trap with the rung that catches it |
| `evals/questions.py` | 34 tasks with gold SQL and per-question invariants |
| `evals/metrics.py` | grading and corpus metrics. Never imported by the loop |
| `evals/report.py` | the table and the charts |

## Exit reasons

```
verified_success    a deterministic check passed
budget_exhausted    iterations, tokens, wall clock or dollars ran out
no_progress         same query fingerprint twice, or stuck at the same rung
hard_blocker        nothing the agent can write will work
escalate            repeated malformed tool calls
model_said_done     naive loop, when the model stops calling tools
```

Tagging every trace with one of these is the highest-signal change in the repo.
Their distribution across a corpus tells you what kind of loop problem you have
before you open a single trace.

## Two kinds of invariant

Invariants divide on one question: could you have written this before anyone
knew the answer?

**Answer-free** ones come from the question and the schema. How many columns the
answer should have. A row count the question states outright. Whether the total
reconciles against a second query written independently. You can write these in
advance, which is the situation the loop is actually in when it runs.

**Calibrated** ones need prior knowledge. A plausible magnitude for quarterly
revenue. A sane ceiling on a customer count. These are fine in production, where
last month's number exists to calibrate against. They are not fine when you
derive them from the answer key of the suite you are about to score.

The `answer_free` arm drops the calibrated set, which is why it appears on the
verification ladder above. The gap between 88% and 93% is what those bands are
worth, and
reporting it is the honest version of a verification claim. `make report` prints
which questions still lean on one.

## Why agent/ never imports evals/

Verification at runtime stops at rung 5. Comparing against a gold answer is
rung 6, and it lives in `evals/`, because at runtime there is no gold answer to
compare to.

```
control plane      rungs 1-5, deterministic, decides whether the loop continues
measurement plane  rung 6 and the LLM judge, offline, decides nothing
```

An LLM judge makes a good metric and a bad gate. The separation between these
two modules is what keeps the model from grading its own homework and then
exiting on the grade. `evals/metrics.py` may not be imported from `agent/`, and
it is not.

## Audit the questions before you trust a number

`make audit` checks every question for three problems that quietly corrupt
results: a LIMIT that cuts a tie, an output shape the question never pinned
down, and invariants too weak to reject a wrong answer.

The first draft of the suite had six of these. They did not look like bugs. They
looked like the agent failing, and they would have gone on a slide.

Run it after adding or editing any question.

## Three kinds of question

The suite mixes questions that have a right answer with questions that do not.
Scoring them together measures two different things at once and hides both. 34
tasks: 30 with an answer, 4 that grade a behaviour.

- **answerable** (easy, medium, trap, hard). Graded against a gold result set,
  order-insensitive, numbers rounded to two decimals and strings case-folded.
- **blocked**. Needs a table the agent may not read, and it is told so through
  an error. Graded on whether the loop stopped instead of rewriting the query
  again.
- **unanswerable**. The data does not exist anywhere in the schema, and nothing
  is denied, so the agent has to work it out from the catalogue. Even the full
  loop is unreliable here. Across the four blocked and unanswerable tasks, three
  trials each, the engineered loop stopped on ten of twelve runs. The naive loop
  submitted an answer on all twelve.

`make report` breaks out all three. The headline solve rate covers all 34
tasks, so correctly refusing an unanswerable question counts as solved.

## Caveats

- 34 hand-written questions against one schema shows a difference in behaviour.
  It is not a benchmark, and the absolute numbers say nothing general about
  text-to-SQL.
- Three trials per arm keeps an eight-point gap clear of the noise. A two-point
  gap needs more. Raise `--trials` if you want tighter numbers.
- The iteration budget is 12 and the tasks are short. That no run exhausted it
  says the other exits fired first on these tasks, not that long-horizon loops
  cannot run away.
- Part of the engineered arm's margin comes from calibrated invariants written
  with the answer key in hand. Quote the `answer_free` number if you want one
  that survives contact with production.
- Prices in `agent/llm.py` were checked in September 2026. Re-check them before
  putting a cost figure anywhere.
- The engineered loop has the same failure it is built to catch, just less often.
  Two runs on one unanswerable question exited `verified_success` with a wrong
  answer. The ladder passed a query that answered a question with no answer.
- The invariants are the interesting part of this repo and every one of them is
  hand written. That is the work, and there is no way around it.

## The outer loop

The inner loop reports a trustworthy exit reason because `should_continue` is
the only exit point. An outer loop can act on that reason to route each
completed run to the right downstream handler. The mapping is:

| exit reason          | route on attempt 1    | route on attempt 2    |
|----------------------|-----------------------|-----------------------|
| verified_success     | shipped               |                       |
| model_said_done      | shipped               |                       |
| hard_blocker         | permissions queue     |                       |
| no_progress          | retry                 | review queue          |
| budget_exhausted     | retry                 | review queue          |
| escalate             | review queue          |                       |

The outer loop has one retry budget per task. A second no_progress or
budget_exhausted goes to the review queue rather than looping again.

### What each arm routes to (TODO: fill from out/routes.txt)

All figures are means across three trials, out of 34 tasks. Format is
mean +/- half-range.

```
                    naive    engineered    (other arms)
shipped correct     TODO       TODO
shipped wrong       TODO       TODO
permissions queue   TODO       TODO
review queue        TODO       TODO
retry pending       TODO       TODO
```

The `shipped wrong` count equals the false success count by construction:
the outer loop ships only on verified_success or model_said_done, the same
exits that define false success in the measurement framework.

### Unanswerable questions (q33, q34)

Q33 asks for customer satisfaction by region. Q34 asks which carrier delivers
fastest. Neither column exists in the schema. The ideal exit is no_progress or
escalate (the agent notices and stops), which routes to retry and then to the
review queue. Hard_blocker routes to the permissions queue, which can only
happen if the agent queries the payments table while investigating these
questions.

```
(TODO: fill per-arm unanswerable summary from out/routes.txt)
arm              to permissions queue   of those: touched payments
naive            TODO                   TODO
engineered       TODO                   TODO
```

Run `make routes | tee out/routes.txt` to produce the numbers above.

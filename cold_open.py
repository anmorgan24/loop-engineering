"""Cold open: one question through the naive loop, no grading shown."""

import sys
from agent.db import connect
from agent.naive import run
from evals.questions import BY_ID

QID = "q24"


def text_of(q):
    for attr in ("text", "question", "prompt"):
        if getattr(q, attr, None):
            return getattr(q, attr)
    raise AttributeError("no question text field on %r" % q)


def main():
    q = BY_ID[QID]
    conn = connect()

    print()
    print("  question")
    print("  " + text_of(q))
    print()

    out = run(text_of(q), question_id=q.id, conn=conn, verbose=True)

    print()
    print("  stopped after %d iterations" % out.iterations)
    print("  exit reason: %s" % out.exit_reason)
    print()
    print("  sql")
    for line in (out.answer_sql or "").strip().splitlines():
        print("  " + line)
    print()
    # On blocked and unanswerable tiers there is no legitimate answer, so
    # there is nothing to print. The interesting part is the exit reason
    # above: the naive loop reports model_said_done on a question it could
    # not have answered.
    if q.tier not in ("blocked", "unanswerable") and out.answer_rows:
        print("  answer")
        for row in out.answer_rows[:10]:
            print("  " + "  ".join(str(c) for c in row))
        print()


if __name__ == "__main__":
    sys.exit(main())

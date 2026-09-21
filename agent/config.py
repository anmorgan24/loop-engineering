"""Feature flags, so each loop design choice can be turned off and measured.

The talk claims five things improve a loop. A technical audience will ask which
one did the work. This file is how you answer that with numbers instead of an
opinion.

Each arm disables exactly one thing relative to `ENGINEERED`, so the difference
between that arm and the full loop is attributable to that one change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LoopConfig:
    label: str = "engineered"

    # Rung 5. With this off the gate accepts anything that executes, which is
    # where most loops sit. Expect solve rate to hold and false success to jump.
    verify_invariants: bool = True

    # Schema-grounded feedback instead of the raw error string.
    classify_errors: bool = True

    # Fingerprint repetition and rung stall.
    detect_no_progress: bool = True

    # Whether a denied table ends the run or is treated as one more error.
    detect_blockers: bool = True

    # Tokens, wall clock and dollars alongside the iteration cap.
    multi_budget: bool = True

    # Calibrated invariants (magnitude bands) need prior knowledge of roughly
    # what the answer should be. Turning them off leaves only the answer-free
    # checks, which are the ones you could honestly have written before seeing
    # any results. The gap between the two arms is the part of the score that
    # depends on already knowing something about the answer.
    use_calibrated: bool = True


ENGINEERED = LoopConfig()

ARMS: dict[str, LoopConfig] = {
    "engineered": ENGINEERED,
    "no_invariants": replace(ENGINEERED, label="no_invariants", verify_invariants=False),
    "no_feedback": replace(ENGINEERED, label="no_feedback", classify_errors=False),
    "no_progress_check": replace(ENGINEERED, label="no_progress_check",
                                 detect_no_progress=False),
    "no_blocker_check": replace(ENGINEERED, label="no_blocker_check",
                                detect_blockers=False),
    "answer_free": replace(ENGINEERED, label="answer_free", use_calibrated=False),
}

# What to run when you want the ablation but not all of it. These three carry
# the talk's three claims.
CORE_ARMS = ["engineered", "answer_free", "no_invariants", "no_feedback"]

# The verification ladder as three points rather than two: no rung 5 at all,
# rung 5 with only what you could write blind, rung 5 with calibration.
VERIFICATION_ARMS = ["no_invariants", "answer_free", "engineered"]

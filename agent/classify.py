"""Error classification.

Four classes, distinguished by one question: can the next action differ in a
way that could succeed?

    TRANSIENT       yes, and the same action will do. Retry. No model call,
                    no iteration charged, no progress charged.
    MALFORMED       yes, and it is cheap. The action was not well formed.
                    Repair it, charge tokens, do not charge progress.
    SEMANTIC        yes, and this is the loop doing its job. The query was
                    well formed and wrong. Feed back, charge progress.
    HARD_BLOCKER    no. Nothing the agent can emit will succeed. Exit now.

The expensive bug is the last one misread as one of the first three. That is
how an agent burns forty iterations and a real amount of money re-issuing a
call against a table it will never be allowed to read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent.verify import Rung, VerifierResult


class ErrorClass(str, Enum):
    NONE = "none"
    TRANSIENT = "transient"
    MALFORMED = "malformed_action"
    SEMANTIC = "semantic_failure"
    HARD_BLOCKER = "hard_blocker"


# Budget treatment per class. Charging every failure against progress is what
# makes a loop exit early on a problem it was about to solve; charging none of
# them is what makes it run forever.
CHARGES_PROGRESS = {
    ErrorClass.NONE: True,
    ErrorClass.TRANSIENT: False,
    ErrorClass.MALFORMED: False,
    ErrorClass.SEMANTIC: True,
    ErrorClass.HARD_BLOCKER: True,
}

MAX_MALFORMED_REPAIRS = 3  # a model that cannot emit valid JSON three times running
                           # is not going to on the fourth


@dataclass
class Classification:
    kind: ErrorClass
    detail: str
    # What actually goes back to the model. Not the same as `detail`.
    feedback: str = ""
    retry_after_s: float = 0.0

    @property
    def charges_progress(self) -> bool:
        return CHARGES_PROGRESS[self.kind]


def classify_exception(e: BaseException) -> Classification:
    """Classify an exception raised by the model provider or the tool layer."""
    name = type(e).__name__
    msg = str(e)

    # Provider side. Names rather than isinstance so the module imports without
    # the SDK present.
    if name in {"RateLimitError", "APIConnectionError", "APITimeoutError",
                "InternalServerError", "APIStatusError"}:
        if "overloaded" in msg.lower() or name != "APIStatusError":
            return Classification(
                ErrorClass.TRANSIENT, f"{name}: {msg}",
                retry_after_s=2.0,
            )

    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return Classification(
            ErrorClass.HARD_BLOCKER,
            f"{name}: {msg}",
            feedback="credentials are missing or rejected",
        )

    if isinstance(e, (json.JSONDecodeError, TypeError, KeyError)):
        return Classification(
            ErrorClass.MALFORMED, f"{name}: {msg}",
            feedback=f"your tool call was not well formed: {msg}",
        )

    return Classification(ErrorClass.SEMANTIC, f"{name}: {msg}", feedback=msg)


def classify_tool_error(tool_name: str, known_tools: set[str], msg: str) -> Classification:
    if tool_name not in known_tools:
        return Classification(
            ErrorClass.MALFORMED,
            f"unknown tool {tool_name!r}",
            feedback=(
                f"there is no tool called {tool_name!r}. "
                f"available tools: {', '.join(sorted(known_tools))}"
            ),
        )
    return Classification(ErrorClass.SEMANTIC, msg, feedback=msg)


def classify_verifier(result: VerifierResult, detect_blockers: bool = True) -> Classification:
    """Map a rung failure onto an error class.

    Note where the line falls. A hallucinated column is SEMANTIC, because the
    schema is right there and the agent can fix it. A denied table is a
    HARD_BLOCKER, because it cannot. Both surface at rung 2, and telling them
    apart is the whole job of this function.
    """
    if result.ok:
        return Classification(ErrorClass.NONE, "", feedback="")

    if result.rung is Rung.PARSE:
        return Classification(
            ErrorClass.MALFORMED, result.detail,
            feedback=f"that SQL does not parse: {result.detail}",
        )

    if result.rung is Rung.RESOLVE:
        if result.detail.startswith("PERMISSION_DENIED") and detect_blockers:
            return Classification(
                ErrorClass.HARD_BLOCKER, result.detail,
                feedback=(
                    "this question needs a table this agent is not permitted to "
                    "read. No rewrite will fix that."
                ),
            )
        return Classification(
            ErrorClass.SEMANTIC, result.detail,
            feedback=f"that query names something that does not exist. {result.detail}",
        )

    if result.rung in (Rung.PLAN, Rung.EXECUTE):
        return Classification(
            ErrorClass.SEMANTIC, result.detail,
            feedback=f"the query was rejected or could not finish: {result.detail}",
        )

    return Classification(
        ErrorClass.SEMANTIC, result.detail,
        feedback=(
            "the query ran but the result failed a correctness check. "
            f"{result.detail}"
        ),
    )


def naive_feedback(result: VerifierResult) -> str:
    """The before picture.

    This is what most loops send back: the raw error, unclassified and
    unaugmented. Compare to the `feedback` fields above, which name the
    available columns and say whether recovery is even possible. The two
    strings cost the same to produce. Their recovery rates are not the same,
    and evals/metrics.py measures the gap.
    """
    return f"Error: {result.detail}"

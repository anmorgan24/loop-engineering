"""The one non-deterministic box in the loop.

Everything else in this package is ordinary code you can unit test. This file
is the part you cannot, which is exactly why the gate, the classifier, and the
verifier live outside it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

MODEL = os.environ.get("SQL_LOOP_MODEL", "claude-sonnet-5")

# USD per million tokens. Verified against Anthropic's pricing page in
# September 2026; re-check before quoting these on a slide.
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

SYSTEM = """You answer questions about a retail database by writing DuckDB SQL.

Work by exploring first: list the tables, describe the ones you need, and run
exploratory queries to check your assumptions about column values. Then submit
one query as your answer.

Things that are true about this database and easy to get wrong:
- order_items.unit_price is the price actually charged. products.list_price is
  the catalogue price. They differ, and order_items.discount applies on top.
- orders.status is one of completed, pending, cancelled, refunded.
- Some customers have no region_id.
- Joining orders to order_items multiplies order rows.

Submit your answer with submit_answer. If the checker rejects it, read the
reason and fix the specific problem it names."""


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    raw_content: list
    stop_reason: Optional[str]

    @property
    def usd(self) -> float:
        inp, out = PRICES.get(MODEL, (0.0, 0.0))
        return (self.input_tokens * inp + self.output_tokens * out) / 1_000_000

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_client: Optional[anthropic.Anthropic] = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        _client = anthropic.Anthropic()
    return _client


def propose_action(messages: list[dict], tools: list[dict],
                   system: str = SYSTEM, max_tokens: int = 1500) -> ModelTurn:
    """One model call. The only source of non-determinism in the loop."""
    resp = client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        tools=tools,
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    calls = [
        {"id": b.id, "name": b.name, "input": b.input}
        for b in resp.content
        if b.type == "tool_use"
    ]
    return ModelTurn(
        text=text,
        tool_calls=calls,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        raw_content=[b.model_dump() for b in resp.content],
        stop_reason=resp.stop_reason,
    )


# --------------------------------------------------------------------------
# Opik shim
# --------------------------------------------------------------------------
# The repo runs with or without Opik configured. Without it, these are no-ops
# and you still get exit reasons on stdout; with it, you get the span tree.

try:
    import opik

    OPIK_AVAILABLE = True

    def track(*args, **kwargs):
        return opik.track(*args, **kwargs)

    def update_trace(**kwargs):
        try:
            opik.update_current_trace(**kwargs)
        except Exception:
            pass

    def update_span(**kwargs):
        try:
            opik.update_current_span(**kwargs)
        except Exception:
            pass

except ImportError:  # pragma: no cover
    OPIK_AVAILABLE = False

    def track(*args, **kwargs):
        def wrap(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return wrap

    def update_trace(**kwargs):
        pass

    def update_span(**kwargs):
        pass

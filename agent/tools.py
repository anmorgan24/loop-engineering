"""Tools the agent can call.

Schema discovery is a tool rather than a prompt dump on purpose. It gives the
trace real structure: early iterations are exploration, later ones are repair,
and you can see the shift in the span tree.
"""

from __future__ import annotations

from typing import Any

from agent.db import DENIED_TABLES, render_schema, schema_map
from agent.verify import PermissionDenied, execute_bounded

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": "List every table in the database with its columns and types.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_table",
        "description": (
            "Show the columns of one table plus a handful of sample rows. Use this "
            "before filtering on a column whose exact values you are guessing at."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    },
    {
        "name": "run_query",
        "description": (
            "Run a read-only SQL query and see the first rows of the result. Use "
            "this to explore. It does not submit an answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "submit_answer",
        "description": (
            "Submit the single query that answers the question. The result is "
            "checked before it is accepted, and you will be told if it fails."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_SPECS}


def _preview(rows, columns, limit: int = 12) -> str:
    if not rows:
        return "(0 rows)"
    head = " | ".join(columns)
    body = "\n".join(
        " | ".join("NULL" if v is None else str(v) for v in r) for r in rows[:limit]
    )
    more = f"\n... {len(rows) - limit} more row(s)" if len(rows) > limit else ""
    return f"{head}\n{body}{more}"


def list_tables(conn) -> str:
    return render_schema(conn)


def describe_table(conn, table: str) -> str:
    table = (table or "").strip().lower()
    if table in DENIED_TABLES:
        raise PermissionDenied(
            f"read access to {table} is not granted to this agent"
        )
    schema = schema_map(conn)
    if table not in schema:
        raise ValueError(
            f"no table named {table!r}. available: {', '.join(sorted(schema))}"
        )
    cols = "\n".join(f"    {c.name} {c.dtype}{' NULL' if c.nullable else ''}"
                     for c in schema[table])
    rows, columns, _ = execute_bounded(conn, f"SELECT * FROM {table} LIMIT 5", timeout_s=5)
    return f"{table}\n{cols}\n\nsample rows:\n{_preview(rows, columns, 5)}"


def run_query(conn, sql: str) -> str:
    from agent.verify import parse, referenced_tables

    tree = parse(sql)
    denied = referenced_tables(tree) & DENIED_TABLES
    if denied:
        raise PermissionDenied(
            f"read access to {', '.join(sorted(denied))} is not granted to this agent"
        )
    rows, columns, truncated = execute_bounded(conn, sql, timeout_s=10, max_rows=200)
    note = "\n(truncated at 200 rows)" if truncated else ""
    return _preview(rows, columns) + note


def dispatch(name: str, args: dict, conn) -> str:
    if name == "list_tables":
        return list_tables(conn)
    if name == "describe_table":
        return describe_table(conn, args.get("table", ""))
    if name == "run_query":
        return run_query(conn, args.get("sql", ""))
    raise ValueError(f"unknown tool {name!r}")

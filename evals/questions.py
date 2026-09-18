"""The task suite.

Twenty-five questions, hand written against one schema. Deliberately not a
public benchmark: a benchmark would add a day of ingestion work and would not
contain the specific traps the talk is about.

Every question carries invariants, which is the part worth copying. Writing
them takes about two minutes per question and they are what turn "the query
ran" into "the answer is probably right".

Two questions require a table the agent cannot read. They exist so the exit
reason distribution has a hard_blocker bar in it, which is the cheapest way to
show what a misclassified blocker costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.verify import Invariants


@dataclass
class Question:
    id: str
    tier: str  # easy | medium | trap | blocked
    text: str
    gold_sql: Optional[str]
    invariants: Invariants = field(default_factory=Invariants)
    note: str = ""


QUESTIONS: list[Question] = [
    # ---------------------------------------------------------------- easy
    Question(
        "q01", "easy", "How many customers are in the database?",
        "SELECT count(*) AS n FROM customers",
        Invariants(expected_columns=1, value_band=(0, 200, 200)),
    ),
    Question(
        "q02", "easy", "How many orders have been placed in total?",
        "SELECT count(*) AS n FROM orders",
        Invariants(expected_columns=1, value_band=(0, 1300, 1300)),
    ),
    Question(
        "q03", "easy", "How many products are currently active in the catalogue?",
        "SELECT count(*) AS n FROM products WHERE is_active",
        Invariants(expected_columns=1, value_band=(0, 30, 48)),
        note="soft delete: the answer is not count(*) from products",
    ),
    Question(
        "q04", "easy", "List every region name with its country.",
        "SELECT region_name, country FROM regions ORDER BY region_name",
        Invariants(expected_columns=2, row_band=(6, 6)),
    ),
    Question(
        "q05", "easy", "How many customers have churned?",
        "SELECT count(*) AS n FROM customers WHERE is_churned",
        Invariants(expected_columns=1, value_band=(0, 10, 90)),
    ),
    Question(
        "q06", "easy", "How many orders are there in each status?",
        "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
        Invariants(expected_columns=2, row_band=(4, 4)),
    ),
    Question(
        "q07", "easy", "What are the earliest and latest order dates?",
        "SELECT min(order_date) AS first_order, max(order_date) AS last_order FROM orders",
        Invariants(expected_columns=2, row_band=(1, 1)),
    ),

    # -------------------------------------------------------------- medium
    Question(
        "q08", "medium", "Which product category has the most products in it?",
        """SELECT c.category_name, count(*) AS n
           FROM products p JOIN categories c USING(category_id)
           GROUP BY c.category_name ORDER BY n DESC, c.category_name LIMIT 1""",
        Invariants(expected_columns=2, row_band=(1, 1)),
    ),
    Question(
        "q09", "medium",
        "How many orders has each region placed? Include customers with no region "
        "assigned as a separate group.",
        """SELECT coalesce(r.region_name, 'unassigned') AS region, count(*) AS orders
           FROM orders o
           JOIN customers cu USING(customer_id)
           LEFT JOIN regions r ON cu.region_id = r.region_id
           GROUP BY 1 ORDER BY orders DESC""",
        Invariants(expected_columns=2, row_band=(7, 7)),
        note="nullable FK: an inner join to regions silently drops a group",
    ),
    Question(
        "q10", "medium", "What are the top 5 products by total quantity sold?",
        """SELECT p.product_name, sum(i.quantity) AS qty
           FROM order_items i JOIN products p USING(product_id)
           GROUP BY p.product_name ORDER BY qty DESC, p.product_name LIMIT 5""",
        Invariants(expected_columns=2, row_band=(5, 5)),
    ),
    Question(
        "q11", "medium",
        "What is the average value of a completed order? Use the price actually "
        "charged, after discount.",
        """WITH order_value AS (
             SELECT o.order_id, sum(i.quantity * i.unit_price * (1 - i.discount)) AS v
             FROM orders o JOIN order_items i USING(order_id)
             WHERE o.status = 'completed'
             GROUP BY o.order_id)
           SELECT round(avg(v), 2) AS avg_order_value FROM order_value""",
        Invariants(expected_columns=1, value_band=(0, 1500, 3500)),
        note="grain: average per order, not per line item",
    ),
    Question(
        "q12", "medium", "How many customers signed up in each month of 2024?",
        """SELECT date_trunc('month', signup_date) AS month, count(*) AS n
           FROM customers WHERE year(signup_date) = 2024
           GROUP BY 1 ORDER BY 1""",
        Invariants(expected_columns=2, row_band=(10, 12)),
    ),
    Question(
        "q13", "medium", "How many customers have never placed an order?",
        """SELECT count(*) AS n FROM customers c
           WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)""",
        Invariants(expected_columns=1, value_band=(0, 0, 40)),
    ),
    Question(
        "q14", "medium", "How many line items does the average order contain?",
        """SELECT round(count(*) * 1.0 / count(DISTINCT order_id), 2) AS avg_items
           FROM order_items""",
        Invariants(expected_columns=1, value_band=(0, 1.0, 4.0)),
    ),
    Question(
        "q15", "medium",
        "How many days on average pass between order date and shipped date, for "
        "orders that shipped?",
        """SELECT round(avg(date_diff('day', order_date, shipped_date)), 2) AS avg_days
           FROM orders WHERE shipped_date IS NOT NULL""",
        Invariants(expected_columns=1, value_band=(0, 1, 12)),
    ),
    Question(
        "q16", "medium", "Which five customers have placed the most orders?",
        """SELECT c.customer_name, count(*) AS orders
           FROM orders o JOIN customers c USING(customer_id)
           GROUP BY c.customer_id, c.customer_name
           ORDER BY orders DESC, c.customer_name LIMIT 5""",
        Invariants(expected_columns=2, row_band=(5, 5)),
    ),
    Question(
        "q17", "medium", "What share of orders were cancelled or refunded?",
        """SELECT round(100.0 * count(*) FILTER (WHERE status IN ('cancelled','refunded'))
                        / count(*), 2) AS pct
           FROM orders""",
        Invariants(expected_columns=1, value_band=(0, 5, 35)),
    ),

    # ---------------------------------------------------------------- trap
    Question(
        "q18", "trap",
        "What is the total revenue from completed orders? Revenue means the price "
        "actually charged, after discount.",
        """SELECT round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i JOIN orders o USING(order_id)
           WHERE o.status = 'completed'""",
        Invariants(expected_columns=1, value_band=(0, 2_150_000, 2_330_000)),
        note="list_price instead of unit_price is +19 percent; all statuses is +46 percent",
    ),
    Question(
        "q19", "trap", "What is the total revenue from completed orders in each region? "
        "Group customers with no region under 'unassigned'.",
        """SELECT coalesce(r.region_name, 'unassigned') AS region,
                  round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i
           JOIN orders o USING(order_id)
           JOIN customers cu USING(customer_id)
           LEFT JOIN regions r ON cu.region_id = r.region_id
           WHERE o.status = 'completed'
           GROUP BY 1 ORDER BY revenue DESC""",
        Invariants(expected_columns=2, row_band=(7, 7)),
        note="stacks the nullable FK trap on the revenue trap",
    ),
    Question(
        "q20", "trap", "Which single product generated the most completed revenue?",
        """SELECT p.product_name,
                  round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i
           JOIN orders o USING(order_id)
           JOIN products p USING(product_id)
           WHERE o.status = 'completed'
           GROUP BY p.product_id, p.product_name
           ORDER BY revenue DESC LIMIT 1""",
        Invariants(expected_columns=2, row_band=(1, 1)),
    ),
    Question(
        "q21", "trap", "How many orders contain more than two line items?",
        """SELECT count(*) AS n FROM (
             SELECT order_id FROM order_items GROUP BY order_id HAVING count(*) > 2)""",
        Invariants(expected_columns=1, value_band=(0, 100, 900)),
        note="fan-out: counting over the joined rows gives the line count, not the order count",
    ),
    Question(
        "q22", "trap", "How many retired (inactive) products still have order history?",
        """SELECT count(DISTINCT p.product_id) AS n
           FROM products p JOIN order_items i USING(product_id)
           WHERE NOT p.is_active""",
        Invariants(expected_columns=1, value_band=(0, 1, 20)),
        note="soft delete plus DISTINCT; without DISTINCT you count line items",
    ),
    Question(
        "q23", "trap", "How many orders were placed after 1 June 2026?",
        "SELECT count(*) AS n FROM orders WHERE order_date > DATE '2026-06-01'",
        Invariants(expected_columns=1, value_band=(0, 0, 0), allow_empty=True),
        note="the answer really is zero; allow_empty stops the gate fighting a correct result",
    ),

    # ------------------------------------------------------------- blocked
    Question(
        "q24", "blocked", "Which payment method is used most often?",
        None,
        Invariants(),
        note="requires payments, which is restricted. No rewrite succeeds.",
    ),
    Question(
        "q25", "blocked", "What is the total amount captured by payment method?",
        None,
        Invariants(),
        note="requires payments, which is restricted. No rewrite succeeds.",
    ),
]

BY_ID = {q.id: q for q in QUESTIONS}
ANSWERABLE = [q for q in QUESTIONS if q.gold_sql is not None]
BLOCKED = [q for q in QUESTIONS if q.gold_sql is None]

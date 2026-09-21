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
        Invariants(expected_columns=1,
                   reconcile_sql="SELECT count(DISTINCT customer_id) FROM customers"),
    ),
    Question(
        "q02", "easy", "How many orders have been placed in total?",
        "SELECT count(*) AS n FROM orders",
        Invariants(expected_columns=1,
                   reconcile_sql="SELECT count(DISTINCT order_id) FROM orders"),
    ),
    Question(
        "q03", "easy", "How many products are currently active in the catalogue?",
        "SELECT count(*) AS n FROM products WHERE is_active",
        Invariants(expected_columns=1, reconcile_sql=(
            "SELECT (SELECT count(*) FROM products) "
            "- (SELECT count(*) FROM products WHERE NOT is_active)")),
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
        Invariants(expected_columns=1, reconcile_sql=(
            "SELECT (SELECT count(*) FROM customers) "
            "- (SELECT count(*) FROM customers WHERE NOT is_churned)")),
    ),
    Question(
        "q06", "easy", "How many orders are there in each status?",
        "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
        Invariants(expected_columns=2, row_band=(4, 4),
                   reconcile_sum=(1, "SELECT count(*) FROM orders")),
    ),
    Question(
        "q07", "easy", "What are the earliest and latest order dates?",
        "SELECT min(order_date) AS first_order, max(order_date) AS last_order FROM orders",
        Invariants(expected_columns=2, row_band=(1, 1)),
    ),

    # -------------------------------------------------------------- medium
    Question(
        "q08", "medium",
        "Which single product category generated the most revenue from completed "
        "orders? Return exactly two columns: the category name and the revenue.",
        """SELECT ct.category_name,
                  round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i
           JOIN orders o USING(order_id)
           JOIN products p USING(product_id)
           JOIN categories ct USING(category_id)
           WHERE o.status = 'completed'
           GROUP BY ct.category_name ORDER BY revenue DESC LIMIT 1""",
        Invariants(expected_columns=2, row_band=(1, 1),
                   value_band=(1, 350_000, 400_000)),
        note="replaced an earlier version that was an 8-way tie; every category "
             "holds exactly 6 products, so 'most products' had no answer",
    ),
    Question(
        "q09", "medium",
        "How many orders has each region placed? Put customers with no region into "
        "their own group and label it exactly 'unassigned'. Return exactly two "
        "columns: the region label and the order count.",
        """SELECT coalesce(r.region_name, 'unassigned') AS region, count(*) AS orders
           FROM orders o
           JOIN customers cu USING(customer_id)
           LEFT JOIN regions r ON cu.region_id = r.region_id
           GROUP BY 1 ORDER BY orders DESC""",
        Invariants(expected_columns=2, row_band=(7, 7),
                   reconcile_sum=(1, "SELECT count(*) FROM orders")),
        note="nullable FK: an inner join to regions silently drops a group, and "
             "reconcile_sum is what notices the parts no longer add to the whole",
    ),
    Question(
        "q10", "medium",
        "What are the top 5 products by total quantity sold on completed orders? "
        "Return exactly two columns: the product name and the quantity.",
        """SELECT p.product_name, sum(i.quantity) AS qty
           FROM order_items i
           JOIN orders o USING(order_id)
           JOIN products p USING(product_id)
           WHERE o.status = 'completed'
           GROUP BY p.product_name ORDER BY qty DESC, p.product_name LIMIT 5""",
        Invariants(expected_columns=2, row_band=(5, 5), value_band=(1, 150, 400)),
        note="'sold' without a status filter has three defensible readings that "
             "produce three different top-5 lists",
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
        "q12", "medium",
        "How many customers signed up in each month of 2024? Return the month as a "
        "date, the first day of that month, and the count.",
        """SELECT date_trunc('month', signup_date) AS month, count(*) AS n
           FROM customers WHERE year(signup_date) = 2024
           GROUP BY 1 ORDER BY 1""",
        Invariants(expected_columns=2, row_band=(10, 12), reconcile_sum=(
            1, "SELECT count(*) FROM customers WHERE year(signup_date) = 2024")),
    ),
    Question(
        "q13", "medium", "How many customers have never placed an order?",
        """SELECT count(*) AS n FROM customers c
           WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)""",
        Invariants(expected_columns=1, reconcile_sql=(
            "SELECT (SELECT count(*) FROM customers) "
            "- (SELECT count(DISTINCT customer_id) FROM orders)")),
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
        "q16", "medium",
        "Which single customer has placed the most orders? Return exactly two "
        "columns: the customer name and the order count.",
        """SELECT c.customer_name, count(*) AS orders
           FROM orders o JOIN customers c USING(customer_id)
           GROUP BY c.customer_id, c.customer_name
           ORDER BY orders DESC LIMIT 1""",
        Invariants(expected_columns=2, row_band=(1, 1), value_band=(1, 10, 30)),
        note="was top-5, which cut a 5-way tie at rank 5; rank 1 is unique",
    ),
    Question(
        "q17", "medium",
        "What share of all orders were cancelled or refunded? Return it as a "
        "percentage between 0 and 100, rounded to two decimal places.",
        """SELECT round(100.0 * count(*) FILTER (WHERE status IN ('cancelled','refunded'))
                        / count(*), 2) AS pct
           FROM orders""",
        Invariants(expected_columns=1, reconcile_sql=(
            "SELECT round(100.0 * (SELECT count(*) FROM orders "
            "WHERE status = 'cancelled' OR status = 'refunded') "
            "/ (SELECT count(*) FROM orders), 2)")),
    ),

    # ---------------------------------------------------------------- trap
    Question(
        "q18", "trap",
        "What is the total revenue from completed orders? Revenue means the price "
        "actually charged, after discount.",
        """SELECT round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i JOIN orders o USING(order_id)
           WHERE o.status = 'completed'""",
        Invariants(expected_columns=1, value_band=(0, 2_150_000, 2_330_000),
                   # band is calibrated; the reconcile below is answer-free
                   reconcile_sql="""WITH per_order AS (
                       SELECT o.order_id,
                              sum(i.quantity * i.unit_price * (1 - i.discount)) AS v
                       FROM orders o JOIN order_items i USING(order_id)
                       WHERE o.status = 'completed' GROUP BY o.order_id)
                     SELECT sum(v) FROM per_order"""),
        note="list_price instead of unit_price is +19 percent; all statuses is +46 percent",
    ),
    Question(
        "q19", "trap",
        "What is the total revenue from completed orders in each region? Put "
        "customers with no region into their own group labelled exactly "
        "'unassigned'. Return exactly two columns: the region label and the revenue.",
        """SELECT coalesce(r.region_name, 'unassigned') AS region,
                  round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
           FROM order_items i
           JOIN orders o USING(order_id)
           JOIN customers cu USING(customer_id)
           LEFT JOIN regions r ON cu.region_id = r.region_id
           WHERE o.status = 'completed'
           GROUP BY 1 ORDER BY revenue DESC""",
        Invariants(expected_columns=2, row_band=(7, 7), reconcile_sum=(
            1, """SELECT sum(i.quantity * i.unit_price * (1 - i.discount))
                  FROM order_items i JOIN orders o USING(order_id)
                  WHERE o.status = 'completed'""")),
        note="stacks the nullable FK trap on the revenue trap; reconcile_sum "
             "catches both a dropped group and a wrong price column",
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
        Invariants(expected_columns=1, reconcile_sql=(
            "SELECT count(*) FROM products p WHERE NOT p.is_active "
            "AND EXISTS (SELECT 1 FROM order_items i "
            "WHERE i.product_id = p.product_id)")),
        note="soft delete plus DISTINCT; without DISTINCT you count line items",
    ),
    Question(
        "q23", "trap", "How many orders were placed after 1 June 2026?",
        "SELECT count(*) AS n FROM orders WHERE order_date > DATE '2026-06-01'",
        Invariants(expected_columns=1, value_band=(0, 0, 0), allow_empty=True),
        note="the answer really is zero; allow_empty stops the gate fighting a correct result",
    ),


    # ----------------------------------------------------------------- hard
    # Added because the first 25 produced zero variance: the engineered loop
    # solved every answerable task in every trial, so no_progress and
    # budget_exhausted never appeared outside the smoke test. A suite with no
    # failures measures the suite, not the loop.
    Question(
        "q26", "hard",
        "For each region, which single product sold the most units on completed "
        "orders? Put customers with no region into a group labelled exactly "
        "'unassigned'. Return exactly three columns: the region label, the "
        "product name, and the units.",
        """WITH units AS (
             SELECT coalesce(r.region_name, 'unassigned') AS region,
                    p.product_name, sum(i.quantity) AS units
             FROM order_items i
             JOIN orders o USING(order_id)
             JOIN products p USING(product_id)
             JOIN customers cu USING(customer_id)
             LEFT JOIN regions r ON cu.region_id = r.region_id
             WHERE o.status = 'completed'
             GROUP BY 1, 2),
           ranked AS (
             SELECT *, row_number() OVER (PARTITION BY region
                                          ORDER BY units DESC, product_name) AS rk
             FROM units)
           SELECT region, product_name, units FROM ranked WHERE rk = 1""",
        Invariants(expected_columns=3, row_band=(7, 7)),
        note="window function plus the nullable-region trap plus a status filter",
    ),
    Question(
        "q27", "hard",
        "How many customers placed at least one order in every quarter of 2025? "
        "Return a single number.",
        """SELECT count(*) AS n FROM (
             SELECT o.customer_id
             FROM orders o WHERE year(o.order_date) = 2025
             GROUP BY o.customer_id
             HAVING count(DISTINCT quarter(o.order_date)) = 4)""",
        Invariants(expected_columns=1, reconcile_sql="""
            SELECT count(*) FROM customers c WHERE NOT EXISTS (
              SELECT 1 FROM (VALUES (1),(2),(3),(4)) AS q(qtr)
              WHERE NOT EXISTS (
                SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
                  AND year(o.order_date) = 2025 AND quarter(o.order_date) = q.qtr))"""),
        note="relational division; the reconcile is written inside out on purpose",
    ),
    Question(
        "q28", "hard",
        "For each month of 2025, what was the completed revenue and how much did "
        "it change from the previous calendar month? Use December 2024 as the "
        "previous month for January 2025. Return exactly three columns: the month "
        "as a date (first of the month), the revenue, and the change.",
        """WITH m AS (
             SELECT date_trunc('month', o.order_date) AS month,
                    sum(i.quantity * i.unit_price * (1 - i.discount)) AS rev
             FROM orders o JOIN order_items i USING(order_id)
             WHERE o.status = 'completed'
             GROUP BY 1),
           lagged AS (
             SELECT month, rev, lag(rev) OVER (ORDER BY month) AS prev FROM m)
           SELECT month, round(rev, 2) AS revenue, round(rev - prev, 2) AS change
           FROM lagged WHERE year(month) = 2025 ORDER BY month""",
        Invariants(expected_columns=3, row_band=(12, 12), reconcile_sum=(
            1, """SELECT sum(i.quantity * i.unit_price * (1 - i.discount))
                  FROM orders o JOIN order_items i USING(order_id)
                  WHERE o.status = 'completed' AND year(o.order_date) = 2025""")),
        note="the lag has to reach outside the filtered range, so the WHERE must "
             "come after the window, not before. An earlier version asked for "
             "2025 only and said to omit January, which had two defensible row "
             "counts and failed in all 15 runs across every arm",
    ),
    Question(
        "q29", "hard",
        "How many distinct pairs of products appear together in five or more "
        "completed orders? Count each unordered pair once. Return a single "
        "number.",
        """SELECT count(*) AS n FROM (
             SELECT i1.product_id AS a, i2.product_id AS b
             FROM order_items i1
             JOIN order_items i2 ON i1.order_id = i2.order_id
                                AND i1.product_id < i2.product_id
             JOIN orders o ON o.order_id = i1.order_id
             WHERE o.status = 'completed'
             GROUP BY 1, 2 HAVING count(DISTINCT i1.order_id) >= 5)""",
        Invariants(expected_columns=1, reconcile_sql="""
            WITH pairs AS (
              SELECT least(i1.product_id, i2.product_id) AS a,
                     greatest(i1.product_id, i2.product_id) AS b,
                     i1.order_id
              FROM order_items i1
              JOIN order_items i2 ON i1.order_id = i2.order_id
                                 AND i1.product_id <> i2.product_id
              JOIN orders o ON o.order_id = i1.order_id
              WHERE o.status = 'completed')
            SELECT count(*) FROM (
              SELECT a, b FROM pairs GROUP BY a, b
              HAVING count(DISTINCT order_id) >= 5)""",
        ),
        note="was 'which pair', which cut a 10-way tie. Self join with an "
             "inequality to dedupe; the usual mistake double counts every pair, "
             "and the reconcile uses least/greatest to get there differently",
    ),
    Question(
        "q30", "hard",
        "How many customers have an average completed order value above the "
        "overall average completed order value? Return a single number.",
        """WITH order_value AS (
             SELECT o.customer_id, o.order_id,
                    sum(i.quantity * i.unit_price * (1 - i.discount)) AS v
             FROM orders o JOIN order_items i USING(order_id)
             WHERE o.status = 'completed'
             GROUP BY o.customer_id, o.order_id),
           per_customer AS (
             SELECT customer_id, avg(v) AS avg_v FROM order_value GROUP BY 1)
           SELECT count(*) AS n FROM per_customer
           WHERE avg_v > (SELECT avg(v) FROM order_value)""",
        Invariants(expected_columns=1, reconcile_sql="""
            WITH ov AS (
              SELECT o.customer_id, o.order_id,
                     sum(i.quantity*i.unit_price*(1-i.discount)) AS v
              FROM orders o JOIN order_items i USING(order_id)
              WHERE o.status='completed' GROUP BY 1, 2)
            SELECT count(*) FROM (
              SELECT customer_id FROM ov GROUP BY customer_id
              HAVING avg(v) > (SELECT avg(v) FROM ov))"""),
        note="nested aggregate at two grains; averaging line items instead of "
             "orders gives a different and wrong answer",
    ),
    Question(
        "q31", "hard",
        "What is the median value of a completed order? Return a single number.",
        """WITH order_value AS (
             SELECT o.order_id,
                    sum(i.quantity * i.unit_price * (1 - i.discount)) AS v
             FROM orders o JOIN order_items i USING(order_id)
             WHERE o.status = 'completed' GROUP BY o.order_id)
           SELECT round(median(v), 2) AS median_order_value FROM order_value""",
        Invariants(expected_columns=1, reconcile_sql="""
            WITH ov AS (
              SELECT o.order_id, sum(i.quantity*i.unit_price*(1-i.discount)) AS v
              FROM orders o JOIN order_items i USING(order_id)
              WHERE o.status='completed' GROUP BY o.order_id)
            SELECT round(quantile_cont(v, 0.5), 2) FROM ov"""),
        note="median at order grain, reconciled against a percentile written "
             "a different way",
    ),
    Question(
        "q32", "hard",
        "Which three regions have the highest share of their orders cancelled or "
        "refunded? Return exactly two columns: the region name and the percentage "
        "between 0 and 100, rounded to two decimals. Exclude customers with no "
        "region.",
        """SELECT r.region_name,
                  round(100.0 * count(*) FILTER (
                    WHERE o.status IN ('cancelled','refunded')) / count(*), 2) AS pct
           FROM orders o
           JOIN customers cu USING(customer_id)
           JOIN regions r ON cu.region_id = r.region_id
           GROUP BY r.region_name ORDER BY pct DESC, r.region_name LIMIT 3""",
        Invariants(expected_columns=2, row_band=(3, 3)),
        note="the one question where excluding the null region is correct, "
             "immediately after two where it is not",
    ),

    # ------------------------------------------------- unanswerable, no denial
    # A blocker that is not a permission error. The data simply is not here.
    # The right behaviour is to notice and stop, and the naive loop cannot.
    Question(
        "q33", "unanswerable",
        "What is the average customer satisfaction rating by region?",
        None, Invariants(),
        note="no satisfaction data exists in this schema and no table is denied; "
             "the agent has to conclude it from the catalogue rather than from "
             "an error message",
    ),
    Question(
        "q34", "unanswerable",
        "Which shipping carrier delivers orders fastest?",
        None, Invariants(),
        note="there is no carrier column anywhere; shipped_date exists, which "
             "makes the question look answerable at a glance",
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

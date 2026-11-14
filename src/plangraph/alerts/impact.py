"""Put a dollar figure on a supply problem by walking the graph.

Question answered here: "if these parts are missing, which open customer
orders cannot ship, and how much revenue is that?"

The graph does the traversal (part -> finished goods -> open orders -> targets).
Python then does the arithmetic, which keeps the Cypher short and the maths testable.

Two figures come out:
- usd_exposed: value of ALL open orders for products that use the part.
- usd_at_risk: the part of that value that stock cannot cover (always <= exposed).
"""

from dataclasses import dataclass, field

import pandas as pd

from plangraph.db import run_read

# Finished goods that use each part, at any BOM depth, with their open orders.
# `*0..4` also matches the part itself, because some purchased items are sold directly.
PART_USAGE = """
MATCH (part:Product) WHERE part.uid IN $part_uids
MATCH path = (fg:Product)-[:HAS_COMPONENT*0..4]->(part)
WHERE EXISTS { (:SalesOrder {status: 'Open'})-[:CONTAINS]->(fg) }
WITH part, fg, sum(reduce(q = 1.0, r IN relationships(path) | q * r.qty)) AS qty_per_unit
MATCH (so:SalesOrder {status: 'Open'})-[l:CONTAINS]->(fg)
RETURN part.uid AS part_uid, part.on_hand AS part_on_hand,
       fg.uid AS fg_uid, fg.name AS fg_name, fg.on_hand AS fg_on_hand,
       qty_per_unit, sum(l.qty) AS open_qty, sum(l.value) AS open_value
"""

# Open orders for a set of finished goods, with territory and the current target.
OPEN_ORDERS = """
MATCH (so:SalesOrder {status: 'Open'})-[l:CONTAINS]->(fg:Product) WHERE fg.uid IN $fg_uids
MATCH (so)-[:IN_TERRITORY]->(t:Territory)
OPTIONAL MATCH (so)-[:SOLD_BY]->(:SalesPerson)-[:HAS_TARGET]->(tg:Target {is_current: true})
RETURN fg.uid AS fg_uid, so.uid AS order_uid, so.due_date AS due_date,
       t.name AS territory, tg.uid AS target_uid, sum(l.value) AS value
"""


@dataclass
class Impact:
    usd_at_risk: float = 0.0
    usd_exposed: float = 0.0
    products_hit: list[str] = field(default_factory=list)  # finished good names
    product_uids: list[str] = field(default_factory=list)
    by_territory: dict[str, float] = field(default_factory=dict)
    by_target: dict[str, float] = field(default_factory=dict)
    earliest_due: object = None  # earliest due date of an at-risk order


def blocked_share(usage: pd.DataFrame) -> pd.Series:
    """For each finished good: the share of its open order value that cannot ship.

    Step 1: units short = open order units - finished units in stock.
    Step 2: each part must cover the build of those short units. If the part's
            stock covers only 25% of that need, 75% of the short units are blocked.
    Step 3: a finished good is blocked by its worst part.
    """
    usage = usage.copy()
    usage["short_qty"] = (usage["open_qty"] - usage["fg_on_hand"]).clip(lower=0)
    usage["part_need"] = usage["short_qty"] * usage["qty_per_unit"]
    total_need = usage.groupby("part_uid")["part_need"].transform("sum")
    covered = (usage["part_on_hand"] / total_need.where(total_need > 0)).clip(upper=1).fillna(1)
    # A part that IS the finished good: its stock was already used in step 1.
    covered = covered.where(usage["part_uid"] != usage["fg_uid"], 0.0)
    usage["blocked"] = (usage["short_qty"] / usage["open_qty"]) * (1 - covered)
    return usage.groupby("fg_uid")["blocked"].max()


def assess(part_uids: list[str]) -> Impact:
    """Revenue impact if the given parts are not available."""
    usage = pd.DataFrame(run_read(PART_USAGE, part_uids=part_uids))
    if usage.empty:
        return Impact()
    blocked = blocked_share(usage)
    orders = pd.DataFrame(run_read(OPEN_ORDERS, fg_uids=list(blocked.index)))
    orders["at_risk"] = orders["value"] * orders["fg_uid"].map(blocked)
    hit = orders[orders["at_risk"] > 0]
    names = usage.drop_duplicates("fg_uid").set_index("fg_uid")["fg_name"]
    hit_by_product = hit.groupby("fg_uid")["at_risk"].sum().sort_values(ascending=False)
    return Impact(
        usd_at_risk=round(float(hit["at_risk"].sum()), 2),
        usd_exposed=round(float(orders["value"].sum()), 2),
        products_hit=[names[uid] for uid in hit_by_product.index],
        product_uids=list(hit_by_product.index),
        by_territory=hit.groupby("territory")["at_risk"].sum().round(2).to_dict(),
        by_target=hit.dropna(subset=["target_uid"])
        .groupby("target_uid")["at_risk"]
        .sum()
        .round(2)
        .to_dict(),
        earliest_due=hit["due_date"].min().to_native() if len(hit) else None,
    )

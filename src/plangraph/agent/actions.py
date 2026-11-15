"""Decision automation: recommend actions for an alert, and record what a planner approves.

Nothing here changes an order or sends an email. Each recommendation is a DRAFT.
When a planner approves one, a Decision node is written and linked to the alert,
which gives an audit trail of who decided what, when and why.
"""

import uuid
from datetime import UTC, datetime

from plangraph.db import run_read, run_write
from plangraph.graph import queries

# Numbers from data/docs/policy_expedite.md and policy_alternate_sourcing.md
EXPEDITE_COST_SHARE = 0.10  # premium freight costs about 10% of the PO value
EXPEDITE_DAYS_SAVED = 6
PLANNER_APPROVAL_LIMIT = 5_000
MAX_PRICE_PREMIUM = 0.10

ALERT_CONTEXT = """
MATCH (a:Alert {uid: $uid})
OPTIONAL MATCH (a)-[:ABOUT]->(po:PurchaseOrder)-[:FROM]->(v:Vendor)
OPTIONAL MATCH (po)-[l:CONTAINS]->(part:Product) WHERE l.is_open
WITH a, po, v, collect({uid: part.uid, name: part.name, qty: l.qty, price: l.unit_price}) AS parts
RETURN a.uid AS uid, a.type AS type, a.title AS title, a.usd_at_risk AS usd_at_risk,
       po.id AS po_id, po.total_value AS po_value, po.days_late AS days_late,
       v.uid AS vendor_uid, v.name AS vendor, parts
LIMIT 1
"""

STOCKOUT_PART = """
MATCH (:Alert {uid: $uid})-[:ABOUT]->(p:Product)
RETURN p.uid AS uid, p.name AS name, p.weekly_demand AS weekly_demand LIMIT 1
"""


def action(
    action_id: str,
    label: str,
    rationale: str,
    cost: float = 0.0,
    days_saved: int | None = None,
    draft: str = "",
) -> dict:
    needs_manager = cost > PLANNER_APPROVAL_LIMIT
    return {
        "id": action_id,
        "label": label,
        "rationale": rationale,
        "est_cost": round(cost, 2),
        "est_days_saved": days_saved,
        "approval": "Supply Manager" if needs_manager else "Planner",
        "draft": draft,
    }


def best_alternate(part_uid: str, current_vendor_uid: str | None, current_price: float | None):
    """Fastest active vendor for the part, other than the current one, within the price rule."""
    rows = run_read(queries.ALTERNATE_VENDORS, product=part_uid, limit=10)
    options = [r for r in rows if r["vendor_uid"] != current_vendor_uid and r["is_active"]]
    if current_price:
        options = [r for r in options if r["price"] <= current_price * (1 + MAX_PRICE_PREMIUM)]
    return min(options, key=lambda r: (r["lead_days"], r["price"]), default=None)


def supply_actions(ctx: dict) -> list[dict]:
    """Actions for a late or at-risk purchase order."""
    actions = []
    for part in [p for p in ctx["parts"] if p["uid"]][:3]:
        alt = best_alternate(part["uid"], ctx["vendor_uid"], part["price"])
        if alt:
            extra = (alt["price"] - part["price"]) * part["qty"]
            actions.append(
                action(
                    f"resource:{part['uid']}:{alt['vendor_uid']}",
                    f"Re-source {part['name']} to {alt['vendor']}",
                    f"{alt['vendor']} can supply it in {alt['lead_days']} days at "
                    f"${alt['price']:.2f} per unit (now ${part['price']:.2f}). "
                    "Within the 10% price rule of the alternate sourcing policy.",
                    cost=max(extra, 0),
                    days_saved=None,
                    draft=f"To {alt['vendor']}: please quote and confirm delivery for "
                    f"{part['qty']:.0f} units of {part['name']} within {alt['lead_days']} days.",
                )
            )
    if not actions:
        actions.append(
            action(
                "note:single_source",
                "No alternate vendor available",
                "The open parts on this order have no other approved vendor inside the price rule "
                "(single source). Expediting is the main option.",
            )
        )
    cost = (ctx["po_value"] or 0) * EXPEDITE_COST_SHARE
    actions.append(
        action(
            f"expedite:{ctx['po_id']}",
            f"Expedite PO {ctx['po_id']} with {ctx['vendor']}",
            f"Premium freight costs about {EXPEDITE_COST_SHARE:.0%} of the order value and saves "
            f"about {EXPEDITE_DAYS_SAVED} days. It protects ${ctx['usd_at_risk']:,.0f} of open orders.",
            cost=cost,
            days_saved=EXPEDITE_DAYS_SAVED,
            draft=f"To {ctx['vendor']}: purchase order {ctx['po_id']} is overdue and is blocking "
            "customer orders. Please ship the open quantity by premium freight at our cost "
            "and confirm the ship date by reply today.",
        )
    )
    return actions


def recommend(alert_uid: str) -> list[dict]:
    """Ranked draft actions for one alert. Cheapest effective option first."""
    rows = run_read(ALERT_CONTEXT, uid=alert_uid)
    if not rows:
        return []
    ctx = rows[0]
    kind = ctx["type"]
    if kind in ("late_po", "po_at_risk"):
        actions = supply_actions(ctx)
    elif kind == "stockout":
        part = run_read(STOCKOUT_PART, uid=alert_uid)[0]
        vendors = run_read(queries.ALTERNATE_VENDORS, product=part["uid"], limit=5)
        best = min(vendors, key=lambda r: (r["lead_days"], r["price"]), default=None)
        actions = []
        if best:
            qty = round((part["weekly_demand"] or 0) * 4)
            actions.append(
                action(
                    f"rush_po:{part['uid']}:{best['vendor_uid']}",
                    f"Place a rush PO for {part['name']} with {best['vendor']}",
                    f"Order 4 weeks of demand ({qty:,} units). Standard lead time is "
                    f"{best['lead_days']} days at ${best['price']:.2f} per unit.",
                    cost=qty * best["price"] * EXPEDITE_COST_SHARE,
                    days_saved=None,
                    draft=f"To {best['vendor']}: please confirm the earliest delivery date for "
                    f"{qty:,} units of {part['name']}, with a rush option.",
                )
            )
        actions.append(
            action(
                f"transfer:{part['uid']}",
                f"Check other locations for {part['name']}",
                "A stock transfer inside the plant takes one day and needs no cost approval.",
            )
        )
    elif kind == "scrap_spike":
        actions = [
            action(
                "quality_hold",
                "Place the work centre on quality hold",
                "The scrap procedure requires a hold and root cause review above 5% scrap.",
            ),
            action(
                "replan_work_orders",
                "Re-plan affected work orders and check component stock",
                "Scrapped units must be rebuilt, which uses extra components.",
            ),
        ]
    elif kind == "demand_surge":
        actions = [
            action(
                "forecast_review",
                "Review the forecast: one-off or new run rate?",
                "The forecast override procedure requires a review within two working days "
                "when demand is above 1.5x forecast.",
            ),
            action(
                "check_components",
                "Check component stock for the surged products",
                "Higher demand pulls more components through the bill of materials.",
            ),
        ]
    else:  # target_gap
        actions = [
            action(
                "prioritise_allocation",
                "Prioritise this territory when allocating short supply",
                "The allocation policy puts orders for under-target territories second, "
                "after earliest due date.",
            ),
            action(
                "escalate_sop",
                "Raise the gap at the S&OP reconciliation meeting",
                "A gap with no supply cause is a demand problem and needs a sales plan.",
            ),
        ]
    # Rank: real actions before notes, then cheapest first.
    return sorted(actions, key=lambda a: (a["id"].startswith("note:"), a["est_cost"]))


def approve(alert_uid: str, action_id: str, approved_by: str, note: str = "") -> dict:
    """Record a planner's approval as a Decision node linked to the alert."""
    chosen = next((a for a in recommend(alert_uid) if a["id"] == action_id), None)
    if chosen is None:
        raise ValueError(f"Action {action_id!r} is not available for alert {alert_uid!r}")
    decision = {
        "uid": f"Decision:{uuid.uuid4().hex[:12]}",
        "action": action_id,
        "label": chosen["label"],
        "est_cost": chosen["est_cost"],
        "approved_by": approved_by,
        "note": note,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    run_write(
        "MATCH (a:Alert {uid: $alert_uid}) "
        "CREATE (d:Decision)-[:RESOLVES]->(a) SET d = $decision, a.resolution = $label",
        alert_uid=alert_uid,
        decision=decision,
        label=chosen["label"],
    )
    return decision

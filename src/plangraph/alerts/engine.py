"""The alert engine: run every rule, price each alert, write Alert nodes to the graph.

Flow for each alert:  rule finds a problem -> impact.assess() walks the graph to
get dollars at risk -> an Alert node is written, linked with ABOUT to what it is
about and with THREATENS to the financial targets it puts at risk.
"""

import json
import logging
from datetime import date

import pandas as pd

from plangraph.alerts import rules
from plangraph.alerts.impact import Impact, assess
from plangraph.db import read_sql, run_read, run_write
from plangraph.graph import queries

log = logging.getLogger(__name__)

SCRAP_RECENT_DAYS = 21
SCRAP_NORMAL_DAYS = 180

OPEN_POS_SQL = """
    SELECT o.purchase_order_id, o.vendor_id, v.name AS vendor, o.due_date, o.days_late,
           o.is_late, COALESCE(r.late_risk, 0) AS late_risk
    FROM stg.purchase_order o
    JOIN stg.vendor v USING (vendor_id)
    LEFT JOIN ml.po_risk r USING (purchase_order_id)
    WHERE o.status = 'Open'"""

LATEST_DEMAND_SQL = """
    SELECT b.product_id, p.subcategory, b.actual, b.qty, b.p90
    FROM ml.demand_backtest b
    JOIN stg.product p USING (product_id)
    WHERE b.period_end = (SELECT MAX(period_end) FROM ml.demand_backtest)"""

SCRAP_SQL = f"""
    SELECT l.location_id, loc.name AS location,
           SUM(w.order_qty)    FILTER (WHERE w.end_date > r.as_of - {SCRAP_RECENT_DAYS}) AS recent_qty,
           SUM(w.scrapped_qty) FILTER (WHERE w.end_date > r.as_of - {SCRAP_RECENT_DAYS}) AS recent_scrapped,
           SUM(w.order_qty)    FILTER (WHERE w.end_date <= r.as_of - {SCRAP_RECENT_DAYS}) AS normal_qty,
           SUM(w.scrapped_qty) FILTER (WHERE w.end_date <= r.as_of - {SCRAP_RECENT_DAYS}) AS normal_scrapped
    FROM stg.work_order w
    JOIN stg.work_order_location l USING (work_order_id)
    JOIN stg.location loc USING (location_id), meta.run_info r
    WHERE w.end_date > r.as_of - {SCRAP_NORMAL_DAYS}
    GROUP BY l.location_id, loc.name"""

SCRAPPED_PRODUCTS_SQL = f"""
    SELECT w.product_id, SUM(w.scrapped_qty) AS scrapped
    FROM stg.work_order w
    JOIN stg.work_order_location l USING (work_order_id), meta.run_info r
    WHERE l.location_id = %(location_id)s AND w.end_date > r.as_of - {SCRAP_RECENT_DAYS}
      AND w.scrapped_qty > 0
    GROUP BY w.product_id ORDER BY scrapped DESC"""


def money(value: float) -> str:
    return f"${value:,.0f}"


def make_alert(
    uid: str,
    kind: str,
    title: str,
    summary: str,
    about: list[str],
    impact: Impact,
    as_of: date,
    urgent: bool = False,
    notable: bool = False,
    **details,
) -> dict:
    """Build one alert as a plain dict. `about` holds the uids the alert points at."""
    early_days = (impact.earliest_due - as_of).days if impact.earliest_due else None
    return {
        "uid": f"Alert:{uid}",
        "type": kind,
        "title": title,
        "summary": summary,
        "severity": rules.severity(impact.usd_at_risk, urgent, notable),
        "usd_at_risk": impact.usd_at_risk,
        "usd_exposed": impact.usd_exposed,
        "early_warning_days": early_days,
        "created": as_of,
        "about": about + impact.product_uids[:10],
        "threatens": impact.by_target,
        "details": json.dumps(
            {
                "products_hit": impact.products_hit[:10],
                "by_territory": impact.by_territory,
                **details,
            },
            default=str,
        ),
    }


def po_parts(po_id: int) -> list[str]:
    rows = run_read(queries.OPEN_PO_PARTS, po_uid=f"PurchaseOrder:{po_id}")
    return [r["uid"] for r in rows]


def po_alerts(as_of: date) -> list[dict]:
    """Rules 1 and 2: POs that are late, or that the model expects to be late."""
    pos = read_sql(OPEN_POS_SQL)
    alerts = []
    for po in rules.late_pos(pos).itertuples():
        impact = assess(po_parts(po.purchase_order_id))
        alerts.append(
            make_alert(
                f"late_po:{po.purchase_order_id}",
                "late_po",
                f"PO {po.purchase_order_id} from {po.vendor} is {po.days_late} days late",
                f"Purchase order {po.purchase_order_id} was due {po.due_date} and has not arrived. "
                f"{money(impact.usd_at_risk)} of open orders cannot ship without it "
                f"({money(impact.usd_exposed)} of open orders use its parts).",
                [f"PurchaseOrder:{po.purchase_order_id}", f"Vendor:{po.vendor_id}"],
                impact,
                as_of,
                urgent=po.days_late >= 14,
                notable=po.days_late >= 7,
                days_late=po.days_late,
            )
        )
    for po in rules.pos_at_risk(pos).itertuples():
        impact = assess(po_parts(po.purchase_order_id))
        alerts.append(
            make_alert(
                f"po_at_risk:{po.purchase_order_id}",
                "po_at_risk",
                f"PO {po.purchase_order_id} from {po.vendor} is likely to arrive late",
                f"Due {po.due_date}. The model gives a {po.late_risk:.0%} chance of late delivery, "
                f"based on this vendor's recent record. If it slips, {money(impact.usd_at_risk)} "
                "of open orders are at risk.",
                [f"PurchaseOrder:{po.purchase_order_id}", f"Vendor:{po.vendor_id}"],
                impact,
                as_of,
                late_risk=round(po.late_risk, 2),
            )
        )
    return alerts


def stockout_alerts(as_of: date) -> list[dict]:
    """Rule 3: purchased items projected to run out."""
    projection = read_sql(
        "SELECT i.*, p.name FROM ml.inventory_projection i JOIN stg.product p USING (product_id)"
    )
    alerts = []
    for item in rules.stockouts(projection).itertuples():
        impact = assess([f"Product:{item.product_id}"])
        week = int(item.stockout_week)
        alerts.append(
            make_alert(
                f"stockout:{item.product_id}",
                "stockout",
                f"{item.name} is projected to run out in week {week}",
                f"On hand {item.on_hand:,} units, forecast demand {item.weekly_demand:,.0f} per week "
                f"({item.weeks_of_cover} weeks of cover) and {item.inbound_qty:,.0f} units on order "
                f"in the next 6 weeks. {money(impact.usd_at_risk)} of open orders are at risk.",
                [f"Product:{item.product_id}"],
                impact,
                as_of,
                urgent=week <= 2,
                stockout_week=week,
                weeks_of_cover=item.weeks_of_cover,
            )
        )
    return alerts


def surge_alerts(as_of: date) -> list[dict]:
    """Rule 4: demand far above forecast."""
    alerts = []
    for group in rules.demand_surges(read_sql(LATEST_DEMAND_SQL)).itertuples():
        uids = [f"Product:{pid}" for pid in group.product_ids]
        impact = assess(uids)
        top = run_read(queries.TOP_TERRITORY_FOR_PRODUCTS, product_uids=uids)
        where = f" Most of it comes from {top[0]['territory']}." if top else ""
        alerts.append(
            make_alert(
                f"demand_surge:{group.subcategory}",
                "demand_surge",
                f"{group.subcategory} demand is {group.ratio:.1f}x the forecast",
                f"{group.actual:,.0f} units ordered in the latest month against a forecast of "
                f"{group.forecast:,.0f}.{where} {money(impact.usd_at_risk)} of open orders are "
                "not covered by finished stock.",
                uids[:10],
                impact,
                as_of,
                ratio=round(group.ratio, 2),
                top_territory=top[0]["territory"] if top else None,
            )
        )
    return alerts


def scrap_alerts(as_of: date) -> list[dict]:
    """Rule 5: a work centre scrapping far more than usual."""
    spikes = rules.scrap_spikes(read_sql(SCRAP_SQL))
    loc = rules.likely_scrap_source(spikes)
    if loc is None:
        return []
    others = sorted(set(spikes["location"]) - {loc["location"]})
    scrapped = read_sql(SCRAPPED_PRODUCTS_SQL, location_id=int(loc["location_id"]))
    uids = [f"Product:{pid}" for pid in scrapped["product_id"]]
    impact = assess(uids)
    also = f" Also elevated on the same routes: {', '.join(others)}." if others else ""
    alert = make_alert(
        f"scrap_spike:{loc['location_id']}",
        "scrap_spike",
        f"{loc['location']} scrap rate is {loc['recent_rate']:.0%} "
        f"(normally {loc['normal_rate']:.1%})",
        f"{loc['recent_scrapped']:,.0f} of {loc['recent_qty']:,.0f} units scrapped in the last "
        f"{SCRAP_RECENT_DAYS} days across {len(uids)} products.{also} "
        f"{money(impact.usd_exposed)} of open orders use these products.",
        [f"Location:{loc['location_id']}"] + uids[:5],
        impact,
        as_of,
        urgent=True,
        recent_rate=round(loc["recent_rate"], 3),
        normal_rate=round(loc["normal_rate"], 4),
        also_elevated=others,
    )
    return [alert]


def target_alerts(as_of: date, supply_alerts: list[dict]) -> list[dict]:
    """Rule 6: territories projected to miss target, after removing revenue at risk.

    Revenue at risk is computed once for all problem parts together, so an order
    hit by two alerts is not counted twice.
    """
    problem_parts = sorted(
        {
            uid
            for a in supply_alerts
            if a["type"] in ("late_po", "stockout")
            for uid in json.loads(a["details"]).get("parts", [])
        }
    )
    combined = assess(problem_parts) if problem_parts else Impact()
    run_write("MATCH (t:Target) SET t.usd_at_risk = 0")
    run_write(
        "UNWIND $rows AS row MATCH (t:Target {uid: row.uid}) SET t.usd_at_risk = row.usd",
        rows=[{"uid": k, "usd": v} for k, v in combined.by_target.items()],
    )

    tracker = pd.DataFrame(run_read(queries.TARGET_TRACKER, territory=None))
    alerts = []
    for row in rules.target_gaps(tracker).itertuples():
        impact = Impact(usd_at_risk=round(row.gap, 2), by_target=dict.fromkeys(row.target_uids, 0))
        cause = (
            f" {money(row.at_risk)} of its open orders are blocked by supply problems."
            if row.at_risk > 0
            else " No supply problem explains it: this is a demand gap."
        )
        alerts.append(
            make_alert(
                f"target_gap:{row.territory}",
                "target_gap",
                f"{row.territory} is projected {row.gap / row.target:.0%} under target",
                f"Target {money(row.target)}; shipped {money(row.shipped)} plus open orders "
                f"{money(row.open)} gives a projection of {money(row.projected)}.{cause}",
                [f"Territory:{row.territory_id}"],
                impact,
                as_of,
                urgent=row.gap > 0.25 * row.target,
                target=row.target,
                projected=row.projected,
                supply_at_risk=row.at_risk,
            )
        )
    return alerts


def add_parts(alerts: list[dict]) -> list[dict]:
    """Record which parts each PO or stockout alert is about (used by target_alerts)."""
    for alert in alerts:
        details = json.loads(alert["details"])
        if alert["type"] == "late_po":
            details["parts"] = po_parts(int(alert["uid"].split(":")[-1]))
        elif alert["type"] == "stockout":
            details["parts"] = [alert["about"][0]]
        alert["details"] = json.dumps(details, default=str)
    return alerts


def write_alerts(alerts: list[dict]) -> None:
    """Upsert Alert nodes. Alerts that no longer fire are marked 'cleared', not deleted."""
    rows = [{k: v for k, v in a.items() if k not in ("about", "threatens")} for a in alerts]
    run_write("MATCH (a:Alert) SET a.status = 'cleared'")
    run_write("MATCH (:Alert)-[r:ABOUT|THREATENS]->() DELETE r")
    run_write(
        "UNWIND $rows AS row MERGE (a:Alert {uid: row.uid}) SET a += row, a.status = 'open'",
        rows=rows,
    )
    about = [{"alert": a["uid"], "target": uid} for a in alerts for uid in a["about"]]
    run_write(
        "UNWIND $rows AS row MATCH (a:Alert {uid: row.alert}) "
        "MATCH (n {uid: row.target}) MERGE (a)-[:ABOUT]->(n)",
        rows=about,
    )
    threatens = [
        {"alert": a["uid"], "target": uid, "usd": usd}
        for a in alerts
        for uid, usd in a["threatens"].items()
    ]
    run_write(
        "UNWIND $rows AS row MATCH (a:Alert {uid: row.alert}) "
        "MATCH (t:Target {uid: row.target}) "
        "MERGE (a)-[r:THREATENS]->(t) SET r.usd_at_risk = row.usd",
        rows=threatens,
    )


def run() -> list[dict]:
    as_of = read_sql("SELECT as_of FROM meta.run_info").iloc[0, 0]
    supply = add_parts(po_alerts(as_of) + stockout_alerts(as_of))
    alerts = supply + surge_alerts(as_of) + scrap_alerts(as_of)
    alerts += target_alerts(as_of, supply)
    write_alerts(alerts)
    counts = pd.Series([a["type"] for a in alerts]).value_counts().to_dict()
    log.info("Alert engine: %d alerts %s", len(alerts), counts)
    return alerts

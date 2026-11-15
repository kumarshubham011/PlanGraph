"""Did the alert engine catch the disruptions we injected?

Compares `meta.scenario_truth` (what we broke on purpose) with the open Alert
nodes in the graph. No LLM involved.

Run:  python -m plangraph.eval.scenario_recall
"""

import json

import pandas as pd

from plangraph.config import get_settings
from plangraph.db import read_sql, run_read

ALERTS = """
MATCH (a:Alert {status: 'open'})
OPTIONAL MATCH (a)-[:ABOUT]->(n)
RETURN a.uid AS uid, a.type AS type, a.usd_at_risk AS usd_at_risk,
       a.early_warning_days AS early_warning_days, collect(n.uid) AS about
"""

# Everything that belongs to a scenario's story: the vendor and parts of the late POs.
RELATED = """
MATCH (po:PurchaseOrder) WHERE po.uid IN $uids
MATCH (po)-[:FROM]->(v:Vendor)
OPTIONAL MATCH (v)-[:SUPPLIES]->(p:Product)
RETURN collect(DISTINCT v.uid) + collect(DISTINCT p.uid) AS uids
"""


def truth_uids(row) -> set[str]:
    return {f"{row.entity_type}:{i}" for i in row.entity_ids.split(",")}


def evaluate() -> dict:
    truth = read_sql("SELECT * FROM meta.scenario_truth")
    alerts = pd.DataFrame(run_read(ALERTS))
    explained = set()  # alerts that belong to an injected scenario
    scenarios = []
    for row in truth.itertuples():
        entities = truth_uids(row)
        story = set(entities)
        if row.entity_type == "PurchaseOrder":
            story |= set(run_read(RELATED, uids=list(entities))[0]["uids"])
        touches_story = alerts["about"].apply(lambda about, s=story: bool(s & set(about)))
        hits = alerts[
            (alerts["type"] == row.expected_alert_type)
            & alerts["about"].apply(lambda about, e=entities: bool(e & set(about)))
        ]
        explained |= set(alerts.loc[touches_story, "uid"])
        warning = hits["early_warning_days"].dropna()
        scenarios.append(
            {
                "scenario": row.scenario,
                "expected_alert": row.expected_alert_type,
                "caught": bool(len(hits)),
                "alerts": len(hits),
                "max_usd_at_risk": float(hits["usd_at_risk"].max()) if len(hits) else 0.0,
                "early_warning_days": int(warning.max()) if len(warning) else None,
            }
        )
    other = alerts[~alerts["uid"].isin(explained)]
    caught = sum(s["caught"] for s in scenarios)
    return {
        "scenarios": scenarios,
        "recall": caught / len(scenarios),
        "open_alerts": len(alerts),
        "alerts_explained_by_scenarios": len(explained),
        # Not injected by us. Some are real findings in the data (e.g. territories
        # under target), the rest is noise a planner would have to dismiss.
        "other_alerts_by_type": other["type"].value_counts().to_dict(),
    }


def main() -> None:
    result = evaluate()
    out_dir = get_settings().artifacts_dir
    out_dir.mkdir(exist_ok=True)
    (out_dir / "scenario_recall.json").write_text(json.dumps(result, indent=2))
    print(pd.DataFrame(result["scenarios"]).to_markdown(index=False))
    print(f"\nRecall: {result['recall']:.0%} of injected scenarios caught")
    print(
        f"Open alerts: {result['open_alerts']}, of which "
        f"{result['alerts_explained_by_scenarios']} belong to an injected scenario"
    )
    print(f"Other alerts by type: {result['other_alerts_by_type']}")


if __name__ == "__main__":
    main()

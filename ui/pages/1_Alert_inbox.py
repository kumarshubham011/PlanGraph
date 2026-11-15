"""Alert inbox: what needs attention, why, and what to do about it."""

import streamlit as st
from api_client import SEVERITY_ICON, TYPE_LABEL, get, money, post, require_api

st.set_page_config(page_title="Alert inbox · PlanGraph", page_icon="🧭", layout="wide")
require_api()
st.title("Alert inbox")

f1, f2, f3 = st.columns(3)
severity = f1.selectbox("Severity", ["all", "high", "medium", "low"])
kind = f2.selectbox("Type", ["all", *TYPE_LABEL], format_func=lambda k: TYPE_LABEL.get(k, "All"))
status = f3.selectbox("Status", ["open", "cleared"])

alerts = get(
    "/alerts",
    status=status,
    **({"severity": severity} if severity != "all" else {}),
    **({"type": kind} if kind != "all" else {}),
)
if not alerts:
    st.info("No alerts match these filters.")
    st.stop()

table = [
    {
        "": SEVERITY_ICON[a["severity"]],
        "Alert": a["title"],
        "Type": TYPE_LABEL[a["type"]],
        "$ at risk": a["usd_at_risk"],
        "Days of warning": a["early_warning_days"],
        "Decision": a["resolution"] or "",
    }
    for a in alerts
]
picked = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_config={"$ at risk": st.column_config.NumberColumn(format="dollar")},
)
st.caption("Select a row to see the evidence and the recommended actions.")

rows = picked.selection.rows
if not rows:
    st.stop()
alert = get(f"/alerts/{alerts[rows[0]]['uid']}")

st.divider()
st.subheader(f"{SEVERITY_ICON[alert['severity']]} {alert['title']}")
st.write(alert["summary"])
k1, k2, k3 = st.columns(3)
k1.metric(
    "Revenue at risk",
    money(alert["usd_at_risk"]),
    help="Open orders that cannot ship: stock does not cover them.",
)
k2.metric(
    "Revenue exposed",
    money(alert["usd_exposed"]),
    help="All open orders for products that use the affected parts (upper bound).",
)
k3.metric(
    "Days of warning",
    alert["early_warning_days"] or "-",
    help="Days until the first affected customer order is due.",
)

left, right = st.columns(2)
with left:
    st.markdown("**What it is about**")
    st.dataframe(
        [{"Type": n["label"], "Name": n["name"]} for n in alert["about"]],
        hide_index=True,
        width="stretch",
    )
    by_territory = alert["details"].get("by_territory") or {}
    if by_territory:
        st.markdown("**Revenue at risk by territory**")
        st.dataframe(
            [
                {"Territory": k, "$ at risk": v}
                for k, v in sorted(by_territory.items(), key=lambda kv: -kv[1])
            ],
            hide_index=True,
            width="stretch",
            column_config={"$ at risk": st.column_config.NumberColumn(format="dollar")},
        )
with right:
    if alert["threatens"]:
        st.markdown("**Targets it threatens**")
        st.dataframe(
            [
                {
                    "Territory": t["territory"],
                    "Sales rep": t["sales_person"],
                    "Target": t["target"],
                    "$ at risk": t["usd_at_risk"],
                }
                for t in alert["threatens"]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "Target": st.column_config.NumberColumn(format="dollar"),
                "$ at risk": st.column_config.NumberColumn(format="dollar"),
            },
        )
    if st.button("Show the impact graph"):
        st.session_state["graph_uid"] = alert["uid"]
        st.switch_page("pages/3_Impact_graph.py")

st.markdown("### Recommended actions")
st.caption(
    "These are drafts. Nothing is sent or changed until you approve, and every "
    "approval is recorded as a Decision in the graph."
)
approver = st.text_input("Your name (recorded with the decision)", value="Demo planner")
for action in alert["actions"]:
    with st.container(border=True):
        st.markdown(f"**{action['label']}**")
        st.write(action["rationale"])
        st.caption(
            f"Estimated cost {money(action['est_cost'])} · approval: {action['approval']}"
            + (
                f" · saves about {action['est_days_saved']} days"
                if action["est_days_saved"]
                else ""
            )
        )
        if action["draft"]:
            st.code(action["draft"], language=None, wrap_lines=True)
        if not action["id"].startswith("note:") and st.button("Approve", key=action["id"]):
            post(
                "/actions/approve",
                {"alert_uid": alert["uid"], "action_id": action["id"], "approved_by": approver},
            )
            st.success("Decision recorded.")
            st.rerun()

if alert["decisions"]:
    st.markdown("### Decisions on this alert (audit trail)")
    st.dataframe(
        [
            {
                "When (UTC)": d["ts"],
                "Action": d["label"],
                "Approved by": d["approved_by"],
                "Est. cost": d["est_cost"],
            }
            for d in alert["decisions"]
        ],
        hide_index=True,
        width="stretch",
    )

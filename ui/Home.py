"""PlanGraph home page: today's picture in a few numbers.

Run with:  streamlit run ui/Home.py
"""

import streamlit as st
from api_client import SEVERITY_ICON, TYPE_LABEL, get, money, require_api

st.set_page_config(page_title="PlanGraph", page_icon="🧭", layout="wide")
health = require_api()

st.title("PlanGraph")
st.caption(
    f"Connected planning copilot · data as of {health['as_of']} · "
    f"{health['graph_nodes']:,} nodes in the graph · "
    + ("LLM agent on" if health["llm_enabled"] else "offline mode (no LLM key)")
)

alerts = get("/alerts")
targets = get("/targets")
total_target = sum(t["target"] for t in targets)
total_projected = sum(t["projected"] for t in targets)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Open alerts", len(alerts))
c2.metric("High severity", sum(a["severity"] == "high" for a in alerts))
c3.metric(
    "Revenue blocked by supply",
    money(sum(t["at_risk"] for t in targets)),
    help="Open order value that cannot ship because parts are missing. "
    "Each order is counted once, even if several alerts point at it.",
)
c4.metric(
    "Projected vs target",
    f"{total_projected / total_target:.0%}",
    help="Shipped + open orders - blocked revenue, for the current quota period.",
)

st.subheader("Top alerts")
for alert in alerts[:5]:
    st.markdown(
        f"{SEVERITY_ICON[alert['severity']]} **{alert['title']}** · "
        f"{TYPE_LABEL[alert['type']]} · {money(alert['usd_at_risk'])} at risk"
    )
st.page_link("pages/1_Alert_inbox.py", label="Open the alert inbox →")

st.subheader("How the last nightly run did")
metrics = get("/metrics")
forecast, late_po = metrics["models"]["demand_forecast"], metrics["models"]["late_po"]
m1, m2, m3 = st.columns(3)
m1.metric(
    "Forecast error (WAPE)",
    f"{forecast['wape_model']:.1%}",
    delta=f"{forecast['wape_model'] - forecast['wape_last_month']:+.1%} vs last-month baseline",
    delta_color="inverse",
)
m2.metric(
    "Late-PO model AUC",
    f"{late_po['auc_model']:.2f}",
    delta=f"{late_po['auc_model'] - late_po['auc_vendor_rate_only']:+.2f} vs vendor-rate baseline",
)
m3.metric("Injected test scenarios", len(metrics["scenarios"]))
with st.expander("What are the injected scenarios?"):
    st.write(
        "Known disruptions are written into the data on purpose, so we can check "
        "that the alert engine catches them."
    )
    st.dataframe(metrics["scenarios"], hide_index=True, width="stretch")

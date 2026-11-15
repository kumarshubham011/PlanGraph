"""Target tracker: are we going to hit the sales targets this quota period?"""

import altair as alt
import pandas as pd
import streamlit as st
from api_client import get, money, require_api

st.set_page_config(page_title="Target tracker · PlanGraph", page_icon="🧭", layout="wide")
require_api()
st.title("Target tracker")

targets = pd.DataFrame(get("/targets"))
period = f"{targets['period_start'].iloc[0]} to {targets['period_end'].iloc[0]}"
st.caption(
    f"Current quota period: {period}. Projected = shipped + open orders - revenue "
    "blocked by supply problems."
)

total = targets[["target", "shipped", "open", "at_risk", "projected"]].sum()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Target", money(total["target"]))
c2.metric("Shipped", money(total["shipped"]))
c3.metric("Open orders", money(total["open"]))
c4.metric(
    "Projected vs target",
    f"{total['projected'] / total['target']:.0%}",
    delta=f"{total['projected'] - total['target']:,.0f} $ vs target",
)

# One stacked bar per territory. Each segment answers "where does the revenue stand?"
# The black tick is the target. Colours: fixed order from a validated palette.
SEGMENTS = {"Shipped": "#2a78d6", "Blocked by supply": "#eb6834", "Open, can ship": "#1baf7a"}
targets["Open, can ship"] = targets["open"] - targets["at_risk"]
bars = targets.rename(columns={"shipped": "Shipped", "at_risk": "Blocked by supply"}).melt(
    id_vars=["territory", "target"],
    value_vars=list(SEGMENTS),
    var_name="status",
    value_name="revenue",
)
bars["order"] = bars["status"].map({name: i for i, name in enumerate(SEGMENTS)})

territories = alt.Y("territory:N", title=None, sort="-x")
stacked = (
    alt.Chart(bars)
    .mark_bar(height=18, stroke="#fcfcfb", strokeWidth=2)
    .encode(
        y=territories,
        x=alt.X("revenue:Q", title="Revenue ($)", axis=alt.Axis(format="~s", gridColor="#e1e0d9")),
        color=alt.Color(
            "status:N",
            title=None,
            scale=alt.Scale(domain=list(SEGMENTS), range=list(SEGMENTS.values())),
            legend=alt.Legend(orient="top"),
        ),
        order="order:Q",
        tooltip=[
            "territory",
            "status",
            alt.Tooltip("revenue:Q", format="$,.0f"),
            alt.Tooltip("target:Q", format="$,.0f"),
        ],
    )
)
target_ticks = (
    alt.Chart(targets)
    .mark_tick(color="#0b0b0b", thickness=3, height=26)
    .encode(
        y=territories,
        x="target:Q",
        tooltip=["territory", alt.Tooltip("target:Q", format="$,.0f", title="target")],
    )
)
st.altair_chart((stacked + target_ticks).properties(height=36 * len(targets) + 60), width="stretch")
st.caption("Black tick = target. A bar that stops short of its tick is a projected miss.")

st.markdown("**The same numbers as a table**")
table = targets[
    ["territory", "target", "shipped", "open", "at_risk", "projected", "gap", "attainment"]
].sort_values("attainment")
dollars = {
    c: st.column_config.NumberColumn(c.replace("_", " ").title(), format="dollar")
    for c in ["target", "shipped", "open", "at_risk", "projected", "gap"]
}
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "territory": "Territory",
        **dollars,
        "attainment": st.column_config.ProgressColumn(
            "Projected / target", format="percent", min_value=0, max_value=1.2
        ),
    },
)
st.info(
    "How to read a gap: if 'Blocked by supply' is large, fix the supply problem in the "
    "alert inbox. If it is small, the territory simply has not sold enough: a demand gap."
)

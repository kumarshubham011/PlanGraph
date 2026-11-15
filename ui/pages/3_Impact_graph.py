"""Impact graph: draw the part of the knowledge graph around one alert or entity."""

import streamlit as st
from api_client import get, require_api
from streamlit_agraph import Config, Edge, Node, agraph

st.set_page_config(page_title="Impact graph · PlanGraph", page_icon="🧭", layout="wide")
require_api()
st.title("Impact graph")

# One colour per node type, in a fixed order (from a colour-blind-safe palette).
COLOURS = {
    "Alert": "#e34948",
    "PurchaseOrder": "#eb6834",
    "Vendor": "#eda100",
    "Product": "#2a78d6",
    "SalesOrder": "#1baf7a",
    "Target": "#6250d6",
    "Territory": "#e87ba4",
    "Location": "#008300",
}
OTHER_COLOUR = "#898781"

alerts = get("/alerts")
options = {a["uid"]: a["title"] for a in alerts}
default_uid = st.session_state.get("graph_uid") or next(iter(options), "")
left, right = st.columns([2, 1])
chosen = left.selectbox(
    "Alert",
    list(options),
    format_func=options.get,
    index=list(options).index(default_uid) if default_uid in options else 0,
)
typed = right.text_input(
    "...or any node uid", placeholder="Product:782, Vendor:1568, PurchaseOrder:2538"
)
uid = typed.strip() or chosen

try:
    subgraph = get(f"/graph/{uid}")
except Exception:
    st.warning(f"No node found with uid {uid!r}.")
    st.stop()

st.caption(" · ".join(f":material/circle: {label}" for label in COLOURS) + " (see colours below)")
legend = st.columns(len(COLOURS))
for column, (label, colour) in zip(legend, COLOURS.items(), strict=True):
    column.markdown(f"<span style='color:{colour}'>●</span> {label}", unsafe_allow_html=True)

nodes = [
    Node(
        id=n["id"],
        label=n["title"][:28],
        title=f"{n['label']}: {n['title']}",
        size=28 if n["id"] == uid else 16,
        color=COLOURS.get(n["label"], OTHER_COLOUR),
    )
    for n in subgraph["nodes"]
]
edges = [Edge(source=e["source"], target=e["target"], label=e["type"]) for e in subgraph["edges"]]
agraph(
    nodes=nodes,
    edges=edges,
    config=Config(width="100%", height=620, directed=True, physics=True, hierarchical=False),
)

st.markdown("**Nodes in this view**")
st.dataframe(
    [{"Type": n["label"], "Name": n["title"], "uid": n["id"]} for n in subgraph["nodes"]],
    hide_index=True,
    width="stretch",
)

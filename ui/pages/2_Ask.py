"""Ask: chat with the planning graph. Every answer shows the queries behind it."""

import streamlit as st
from api_client import post, require_api

st.set_page_config(page_title="Ask · PlanGraph", page_icon="🧭", layout="wide")
health = require_api()
st.title("Ask PlanGraph")
if not health["llm_enabled"]:
    st.info(
        "Offline mode: no GEMINI_API_KEY is set, so questions are routed to one fixed "
        "query by keyword. Add a key to .env for full multi-step answers."
    )

EXAMPLES = [
    "Which late purchase orders hurt revenue the most?",
    "Why is Canada tracking under quota? Is it supply or demand?",
    "Is there an alternate vendor for Metal Sheet 2?",
    "Which work centres are scrapping the most?",
    "What is our expedite policy?",
]


def show_steps(result: dict) -> None:
    """The audit trail of an answer: each tool call, its Cypher and its rows."""
    for number, step in enumerate(result["steps"], start=1):
        label = f"Step {number}: {step['tool']}" + (
            " (LLM-written Cypher)" if step["generated"] else ""
        )
        with st.expander(label):
            if step["args"]:
                st.json(step["args"], expanded=False)
            st.code(step["cypher"], language="cypher")
            if step["error"]:
                st.error(step["error"])
            if step["rows"]:
                st.dataframe(step["rows"], hide_index=True, width="stretch")
    if result["evidence_uids"]:
        st.caption("Evidence nodes: " + ", ".join(result["evidence_uids"][:12]))
    if result["input_tokens"]:
        st.caption(f"Tokens: {result['input_tokens']:,} in, {result['output_tokens']:,} out")


if "chat" not in st.session_state:
    st.session_state["chat"] = []  # list of (question, result)

cols = st.columns(len(EXAMPLES))
clicked = next((q for col, q in zip(cols, EXAMPLES, strict=True) if col.button(q)), None)
question = st.chat_input("Ask about orders, vendors, stock, scrap, targets or policies") or clicked

for past_question, past_result in st.session_state["chat"]:
    st.chat_message("user").write(past_question)
    with st.chat_message("assistant"):
        st.markdown(past_result["answer"])
        show_steps(past_result)

if question:
    st.chat_message("user").write(question)
    with st.chat_message("assistant"):
        with st.spinner("Querying the graph..."):
            try:
                result = post("/ask", {"question": question})
            except Exception as exc:
                st.error(f"The question failed: {exc}")
                st.stop()
        st.markdown(result["answer"])
        show_steps(result)
    st.session_state["chat"].append((question, result))

"""The agent, as a LangGraph state machine with two steps that loop:

    START -> model -> (tool calls?) -> tools -> model -> ... -> END

- model: the LLM reads the conversation and either asks for tools or answers.
- tools: runs the tools the LLM asked for and appends the results.
The loop ends when the LLM answers without asking for a tool.
"""

from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from plangraph.agent import offline
from plangraph.agent.llm import get_llm, message_text
from plangraph.agent.tools import ALL_TOOLS
from plangraph.config import get_settings

MAX_STEPS = 12  # hard stop so a confused model cannot loop forever

SYSTEM_PROMPT = """You are PlanGraph, a planning copilot for a bicycle manufacturer.
You answer questions from supply planners, demand planners, S&OP leads and operations
managers using the company's knowledge graph and documents.

How to work:
- Always use tools to get facts. Never state a number that did not come from a tool result.
- Start with the most specific tool. Use graph_query only when no other tool fits.
- For "why" questions, follow the chain: target -> open orders -> products -> parts ->
  purchase orders / vendors / work orders. Check both supply (alerts, late POs, stock,
  scrap) and demand (target_variance) before concluding.
- For policy or "what should we do" questions, also search the documents.

How to answer:
- Lead with the answer in one or two sentences, then the key figures.
- Say whether a problem is a supply problem or a demand problem when that is the question.
- "usd_at_risk" is revenue that cannot ship. "exposed" is an upper bound. Do not mix them up.
- Name the entities you relied on (PO numbers, vendors, products, territories).
- If the tools return nothing useful, say so plainly. Do not guess.
- Keep it short and use plain language. Amounts in dollars, rounded.
"""


class AgentState(TypedDict):
    # add_messages appends new messages to the list instead of replacing it
    messages: Annotated[list, add_messages]


def build_agent(llm: BaseChatModel, tools: list | None = None):
    """Wire the two-step loop. `llm` is passed in so tests can use a fake model."""
    tools = tools or ALL_TOOLS
    model = llm.bind_tools(tools)

    def call_model(state: AgentState) -> dict:
        reply = model.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)  # tool calls -> "tools", else END
    graph.add_edge("tools", "model")
    return graph.compile()


@lru_cache
def default_agent():
    return build_agent(get_llm())


def collect_uids(value) -> set[str]:
    """Find every graph uid mentioned in tool rows (any key ending in 'uid')."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("uid") and isinstance(item, str):
                found.add(item)
            else:
                found |= collect_uids(item)
    elif isinstance(value, list):
        for item in value:
            found |= collect_uids(item)
    return found


def summarise_run(messages: list, mode: str) -> dict:
    """Turn the message history into the response the API returns."""
    calls = {c["id"]: c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    steps, evidence = [], set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        artifact = message.artifact or {}
        rows = artifact.get("rows", [])
        evidence |= collect_uids(rows)
        steps.append(
            {
                "tool": message.name,
                "args": calls.get(message.tool_call_id, {}).get("args", {}),
                "cypher": artifact.get("cypher", ""),
                "generated": artifact.get("generated", False),
                "error": artifact.get("error"),
                "rows": rows,
            }
        )
    usage = [m.usage_metadata for m in messages if isinstance(m, AIMessage) and m.usage_metadata]
    return {
        "answer": message_text(messages[-1]),
        "mode": mode,
        "steps": steps,
        "evidence_uids": sorted(evidence),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
    }


def ask(question: str, agent=None) -> dict:
    """Answer a question. Uses the LLM agent when a key is set, otherwise offline routing."""
    if agent is None and not get_settings().llm_enabled:
        return offline.answer(question)
    agent = agent or default_agent()
    result = agent.invoke({"messages": [HumanMessage(question)]}, {"recursion_limit": MAX_STEPS})
    return summarise_run(result["messages"], mode="agent")

"""Request and response shapes for the API (validated by Pydantic)."""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class Step(BaseModel):
    tool: str
    args: dict
    cypher: str
    generated: bool = False  # True when the Cypher was written by the LLM
    error: str | None = None
    rows: list[dict]


class AskResponse(BaseModel):
    answer: str
    mode: str  # "agent" (LLM) or "offline" (keyword routing)
    steps: list[Step]
    evidence_uids: list[str]
    input_tokens: int = 0
    output_tokens: int = 0


class ApproveRequest(BaseModel):
    alert_uid: str
    action_id: str
    approved_by: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class GraphNode(BaseModel):
    id: str
    label: str
    title: str
    props: dict


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str


class Subgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]

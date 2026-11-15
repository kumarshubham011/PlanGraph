"""Text2Cypher: let the LLM write a Cypher query for questions no template covers.

This is the fallback, not the main path. It is less reliable than the templates,
so every generated query goes through the guardrails and a read-only session.
If the query fails, the error is shown to the LLM once so it can fix it.
"""

from langchain_core.language_models import BaseChatModel

from plangraph.agent.guardrails import UnsafeCypherError, clean_rows, validate_cypher
from plangraph.agent.llm import message_text
from plangraph.db import as_of_date, run_read

GRAPH_SCHEMA = """
Nodes (every node also has `uid`):
(:Product {id, sku, name, category, subcategory, list_price, standard_cost, is_finished_good,
           is_manufactured, is_purchased, on_hand, weekly_demand, weeks_of_cover, stockout_week})
(:Vendor {id, name, rating, is_preferred, is_active})   // rating 1 = best, 5 = worst
(:Location {id, name})
(:PurchaseOrder {id, status, order_date, due_date, received_date, total_value, days_late,
                 is_late, late_risk})                   // status: Open | Received | Rejected
(:SalesOrder {id, status, order_date, due_date, ship_date, is_online, total_value})
                                                        // status: Open | Shipped
(:Territory {id, name, region})
(:SalesPerson {id, name})
(:Target {id, period_start, period_end, amount, is_current, usd_at_risk})
(:WorkOrder {id, status, order_qty, scrapped_qty, start_date, end_date, due_date})
(:ScrapReason {id, name})
(:Forecast {period_start, period_end, horizon, qty, p10, p90})
(:Alert {type, severity, title, summary, usd_at_risk, usd_exposed, status, created})
(:Decision {action, label, approved_by, ts, note})

Relationships:
(:Product)-[:HAS_COMPONENT {qty}]->(:Product)        // assembly -> component (bill of materials)
(:Vendor)-[:SUPPLIES {lead_days, price}]->(:Product)
(:PurchaseOrder)-[:FROM]->(:Vendor)
(:PurchaseOrder)-[:CONTAINS {qty, received_qty, unit_price, due_date, is_open, days_late}]->(:Product)
(:SalesOrder)-[:CONTAINS {qty, value}]->(:Product)
(:SalesOrder)-[:IN_TERRITORY]->(:Territory)
(:SalesOrder)-[:SOLD_BY]->(:SalesPerson)
(:SalesPerson)-[:COVERS]->(:Territory)
(:SalesPerson)-[:HAS_TARGET]->(:Target)
(:Product)-[:STOCKED_AT {on_hand}]->(:Location)
(:WorkOrder)-[:PRODUCES]->(:Product)
(:WorkOrder)-[:SCRAPPED_FOR]->(:ScrapReason)
(:WorkOrder)-[:ROUTED_THROUGH]->(:Location)
(:Product)-[:HAS_FORECAST]->(:Forecast)
(:Alert)-[:ABOUT]->(any node)
(:Alert)-[:THREATENS {usd_at_risk}]->(:Target)
(:Decision)-[:RESOLVES]->(:Alert)
"""

PROMPT = """You write Cypher for a Neo4j graph of a bicycle manufacturer's planning data.

Schema:
{schema}

Rules:
- Read-only: use MATCH / OPTIONAL MATCH / WITH / WHERE / RETURN / ORDER BY / LIMIT only.
- Use only the labels, relationship types and properties in the schema.
- Match names case-insensitively: toLower(n.name) CONTAINS toLower('text').
- Dates are Neo4j dates. Today is date('{as_of}'). Do not use date() with no argument.
- Return named columns (use AS), at most 25 rows.
- Reply with the Cypher query only, no explanation.

Example
Question: Which vendors have the most open purchase orders?
Cypher: MATCH (po:PurchaseOrder {{status: 'Open'}})-[:FROM]->(v:Vendor)
RETURN v.name AS vendor, count(po) AS open_orders ORDER BY open_orders DESC LIMIT 10

Question: {question}
{feedback}Cypher:"""


def generate_and_run(question: str, llm: BaseChatModel, max_attempts: int = 2) -> dict:
    """Returns {"cypher", "rows"} on success or {"cypher", "error"} if every attempt fails."""
    feedback, cypher, error = "", "", "no attempt made"
    for _ in range(max_attempts):
        prompt = PROMPT.format(
            schema=GRAPH_SCHEMA, question=question, feedback=feedback, as_of=as_of_date()
        )
        cypher = message_text(llm.invoke(prompt))
        try:
            cypher = validate_cypher(cypher)
            return {"cypher": cypher, "rows": clean_rows(run_read(cypher, timeout=10))}
        except UnsafeCypherError as exc:
            error = f"Rejected by guardrails: {exc}"
        except Exception as exc:  # syntax error, unknown property, timeout...
            error = f"Neo4j error: {str(exc)[:300]}"
        feedback = f"Your previous query was:\n{cypher}\nIt failed with: {error}\nFix it.\n"
    return {"cypher": cypher, "error": error}

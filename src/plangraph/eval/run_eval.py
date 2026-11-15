"""Compare three ways of answering the 40 golden questions.

1. docs_only    - plain RAG: vector search over the documents, nothing else.
2. graph_router - single-shot GraphRAG: keyword routing picks ONE templated graph
                  query (the offline mode).
3. agent        - the LangGraph agent: plans, calls several tools, writes the answer.

With a Gemini key, approaches 1 and 2 let the LLM write the final answer from
what was retrieved, and approach 3 runs fully. Without a key, 1 and 2 are graded
on the retrieved text itself ("was the right fact fetched?") and 3 is skipped.

Run:  python -m plangraph.eval.run_eval
"""

import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd
import yaml

from plangraph.agent import offline
from plangraph.agent.graph import ask
from plangraph.config import get_settings
from plangraph.db import as_of_date, run_read
from plangraph.graph.documents import search_documents

QUESTIONS_FILE = Path(__file__).with_name("golden_questions.yaml")
NUMBER = re.compile(r"(\d[\d,]*\.?\d*)\s*(k|m|million|thousand)?\b", re.IGNORECASE)
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}
TOLERANCE = 0.02  # a number counts as right within 2% (answers are often rounded)

ANSWER_PROMPT = """Answer the question using only the context. Be brief and include the
exact names and figures from the context. If the context does not contain the answer, say so.

Question: {question}

Context:
{context}"""


def numbers_in(text: str) -> list[float]:
    """Every number in a text, so '$320k', '319,813' and '0.32 million' all parse."""
    found = []
    for digits, unit in NUMBER.findall(text):
        try:
            found.append(float(digits.replace(",", "")) * SCALE.get((unit or "").lower(), 1))
        except ValueError:
            continue
    return found


def contains(answer: str, expected) -> bool:
    """Is the expected value in the answer? Text: substring. Number: within tolerance."""
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        return any(abs(n - expected) <= TOLERANCE * abs(expected) for n in numbers_in(answer))
    return str(expected).lower() in answer.lower()


def grade(answer: str, expected: list, match: str = "all") -> bool:
    if not expected:
        return False
    hits = [contains(answer, value) for value in expected]
    return any(hits) if match == "any" else all(hits)


def expected_values(question: dict) -> list:
    """Static keywords, or the result of the question's truth query on the graph."""
    if "keywords" in question:
        return question["keywords"]
    rows = run_read(question["truth"], as_of=as_of_date())
    return [row["expected"] for row in rows if row["expected"] is not None]


def llm_answer(question: str, context: str) -> tuple[str, int]:
    from plangraph.agent.llm import get_llm, message_text

    reply = get_llm().invoke(ANSWER_PROMPT.format(question=question, context=context))
    usage = reply.usage_metadata or {}
    return message_text(reply), usage.get("total_tokens", 0)


def docs_only(question: str, use_llm: bool) -> dict:
    context = "\n\n".join(chunk["text"] for chunk in search_documents(question, k=4))
    answer, tokens = llm_answer(question, context) if use_llm else (context, 0)
    return {"answer": answer, "tokens": tokens, "valid_cypher": None}


def graph_router(question: str, use_llm: bool) -> dict:
    result = offline.answer(question)
    context = json.dumps(result["steps"][0]["rows"], default=str)
    answer, tokens = llm_answer(question, context) if use_llm else (context, 0)
    return {"answer": answer, "tokens": tokens, "valid_cypher": None}


def agent(question: str, use_llm: bool) -> dict:
    result = ask(question)
    generated = [s for s in result["steps"] if s["generated"]]
    valid = all(s["error"] is None for s in generated) if generated else None
    return {
        "answer": result["answer"],
        "valid_cypher": valid,
        "tokens": result["input_tokens"] + result["output_tokens"],
    }


APPROACHES = {"docs_only": docs_only, "graph_router": graph_router, "agent": agent}


def run(approaches: list[str]) -> pd.DataFrame:
    use_llm = get_settings().llm_enabled
    questions = yaml.safe_load(QUESTIONS_FILE.read_text())
    records = []
    for question in questions:
        expected = expected_values(question)
        for name in approaches:
            started = time.perf_counter()
            try:
                output = APPROACHES[name](question["question"], use_llm)
            except Exception as exc:  # one failed question must not stop the run
                output = {"answer": f"ERROR: {exc}", "tokens": 0, "valid_cypher": None}
            records.append(
                {
                    "id": question["id"],
                    "category": question["category"],
                    "approach": name,
                    "correct": grade(output["answer"], expected, question.get("match", "all")),
                    "seconds": round(time.perf_counter() - started, 2),
                    "tokens": output["tokens"],
                    "valid_cypher": output["valid_cypher"],
                    "question": question["question"],
                    "expected": expected,
                    "answer": output["answer"][:500],
                }
            )
    return pd.DataFrame(records)


def summarise(results: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    overall = (
        results.groupby("approach")
        .agg(
            accuracy=("correct", "mean"),
            p50_seconds=("seconds", "median"),
            avg_tokens=("tokens", "mean"),
            valid_cypher=("valid_cypher", "mean"),
        )
        .round(3)
    )
    by_category = results.pivot_table(
        index="category", columns="approach", values="correct", aggfunc="mean"
    ).round(2)
    return overall, by_category


def main() -> None:
    use_llm = get_settings().llm_enabled
    default = list(APPROACHES) if use_llm else ["docs_only", "graph_router"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approaches", nargs="+", choices=list(APPROACHES), default=default)
    args = parser.parse_args()

    results = run(args.approaches)
    overall, by_category = summarise(results)
    mode = "LLM writes the answers" if use_llm else "no LLM: graded on retrieved text"
    out_dir = get_settings().artifacts_dir
    out_dir.mkdir(exist_ok=True)
    results.to_json(out_dir / "eval_results.json", orient="records", indent=2)
    print(f"Mode: {mode}\n")
    print(overall.to_markdown(), "\n")
    print("Accuracy by question category:\n")
    print(by_category.to_markdown())


if __name__ == "__main__":
    main()

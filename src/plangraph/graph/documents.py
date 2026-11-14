"""Load the markdown documents into the graph as DocChunk nodes with embeddings.

Each document is split into chunks, each chunk gets a vector, and a Neo4j vector
index makes "find the chunks closest to this question" fast. Chunks are also
linked to the vendors they mention, which connects text to the structured graph.
"""

import logging
from pathlib import Path

from plangraph.config import get_settings
from plangraph.db import run_read, run_write
from plangraph.embeddings import DIMENSIONS, embed_documents, embed_query
from plangraph.graph.loader import merge_nodes

log = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 900

VECTOR_INDEX = f"""
CREATE VECTOR INDEX doc_chunks IF NOT EXISTS FOR (c:DocChunk) ON (c.embedding)
OPTIONS {{indexConfig: {{`vector.dimensions`: {DIMENSIONS},
                         `vector.similarity_function`: 'cosine'}}}}
"""

SEARCH = """
CALL db.index.vector.queryNodes('doc_chunks', $k, $embedding) YIELD node, score
OPTIONAL MATCH (node)-[:MENTIONS]->(v:Vendor)
RETURN node.uid AS uid, node.title AS title, node.source AS source, node.text AS text,
       round(score, 3) AS score, collect(v.name) AS vendors
ORDER BY score DESC
"""


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split on blank lines and pack paragraphs together up to max_chars per chunk."""
    chunks, current = [], ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def read_chunks(docs_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text()
        title = text.splitlines()[0].lstrip("# ").strip()
        for number, chunk in enumerate(split_into_chunks(text)):
            # Repeat the title in every chunk so a chunk makes sense on its own.
            body = chunk if number == 0 else f"{title}\n\n{chunk}"
            rows.append(
                {
                    "uid": f"DocChunk:{path.stem}:{number}",
                    "source": path.name,
                    "title": title,
                    "text": body,
                }
            )
    return rows


def load_documents() -> None:
    rows = read_chunks(get_settings().docs_dir)
    vectors = embed_documents([r["text"] for r in rows])
    for row, vector in zip(rows, vectors, strict=True):
        row["embedding"] = vector
    run_write("DROP INDEX doc_chunks IF EXISTS")
    merge_nodes("DocChunk", rows)
    run_write(VECTOR_INDEX)
    run_write("CALL db.awaitIndexes(60)")
    run_write(
        "MATCH (c:DocChunk), (v:Vendor) WHERE c.text CONTAINS v.name MERGE (c)-[:MENTIONS]->(v)"
    )
    log.info("Loaded %d document chunks", len(rows))


def search_documents(question: str, k: int = 4) -> list[dict]:
    """Vector search: the k chunks whose embedding is closest to the question's."""
    return run_read(SEARCH, k=k, embedding=embed_query(question))

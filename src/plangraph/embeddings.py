"""Turn text into vectors (embeddings) for document search.

With a Gemini API key we use Gemini embeddings, which capture meaning
("late delivery" is close to "delayed shipment"). Without a key we fall back to
a simple local method that only matches shared words, so the app still runs.

Documents and questions must be embedded by the same method, so the vector
index is rebuilt whenever the pipeline runs.
"""

import hashlib
import math
import re

from plangraph.config import get_settings

DIMENSIONS = 768


def hash_embed(text: str) -> list[float]:
    """Offline fallback: feature hashing of words and word pairs.

    Each word is hashed to one of DIMENSIONS slots and adds 1 there. Texts that
    share words end up with similar vectors. It is deterministic and needs no model.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    tokens = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    vector = [0.0] * DIMENSIONS
    for token in tokens:
        slot = int(hashlib.md5(token.encode()).hexdigest(), 16) % DIMENSIONS
        vector[slot] += 1.0
    length = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / length for v in vector]


def _gemini():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    settings = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=settings.gemini_embedding_model,
        google_api_key=settings.gemini_api_key,
        output_dimensionality=DIMENSIONS,
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    if get_settings().llm_enabled:
        return _gemini().embed_documents(texts, task_type="RETRIEVAL_DOCUMENT")
    return [hash_embed(t) for t in texts]


def embed_query(text: str) -> list[float]:
    if get_settings().llm_enabled:
        return _gemini().embed_query(text, task_type="RETRIEVAL_QUERY")
    return hash_embed(text)

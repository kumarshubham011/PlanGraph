"""The only place that knows which LLM provider we use.

To switch from Gemini to another provider, change get_llm() and nothing else:
the rest of the code only needs a LangChain chat model.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from plangraph.config import get_settings


@lru_cache
def get_llm() -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    if not settings.llm_enabled:
        raise RuntimeError("No GEMINI_API_KEY set. The app is running in offline mode.")
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,  # same question -> same answer, as far as the model allows
        max_retries=2,
        timeout=60,
    )


def message_text(message) -> str:
    """Text of a chat message. Gemini sometimes returns a list of parts instead of a string."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [p if isinstance(p, str) else p.get("text", "") for p in content]
    return "".join(parts)

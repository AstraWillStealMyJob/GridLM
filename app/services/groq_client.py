import os
from functools import lru_cache

from groq import Groq


@lru_cache(maxsize=1)
def get_groq_client() -> Groq:
    """Create the Groq client lazily so /health does not require the API key."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return Groq(api_key=api_key)


def get_groq_model() -> str:
    return os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

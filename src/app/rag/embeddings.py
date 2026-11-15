"""Embedding providers behind our own interface (spec 07)."""

import hashlib
import math
import re
import unicodedata
from typing import Protocol

from app.core.config import get_settings


class EmbeddingsUnavailableError(RuntimeError):
    """No embedding provider is configured (e.g. missing OPENAI_API_KEY)."""


class Embedder(Protocol):
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """`langchain-openai` `OpenAIEmbeddings` (the import is lazy and optional)."""

    def __init__(self, model: str, dim: int, api_key: str) -> None:
        from langchain_openai import OpenAIEmbeddings

        self.model = model
        self.dim = dim
        self._client = OpenAIEmbeddings(model=model, dimensions=dim, api_key=api_key)  # type: ignore[arg-type]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    "para como que los las del una unos unas por con sus mis tus tengo puedo "
    "este esta esto son ser hay muy mas pero sin sobre entre desde hasta".split()
)


def _terms(text: str) -> list[str]:
    """Accent-folded, stopword-free, 5-letter-stemmed words (crude Spanish stemmer)."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    words = _TOKEN.findall(folded)
    return [w[:5] for w in words if len(w) > 2 and w not in _STOP]


class FakeEmbeddings:
    """Deterministic bag-of-words hashing embedder for tests (no network).

    Texts sharing words get a high cosine similarity, so retrieval is meaningful.
    """

    def __init__(self, dim: int | None = None, model: str = "fake") -> None:
        self.dim = dim or get_settings().embedding_dim
        self.model = model

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for word in _terms(text):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise EmbeddingsUnavailableError("OPENAI_API_KEY is not set")
    return OpenAIEmbedder(
        settings.embedding_model,
        settings.embedding_dim,
        settings.openai_api_key.get_secret_value(),
    )

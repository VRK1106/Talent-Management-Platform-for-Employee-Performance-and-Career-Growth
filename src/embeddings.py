"""Embedding utilities built on ``BAAI/bge-large-en-v1.5``.

Design notes (these materially affect retrieval quality):

* The model is loaded once per session via a global singleton.
* Device auto-selects CUDA when available, else CPU.
* Embeddings are always L2-normalized (``normalize_embeddings=True``) to pair
  correctly with the cosine-distance Chroma collection.
* Queries — and only queries — get the BGE instruction prefix.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer
from src.config import EMBEDDING_MODEL, QUERY_INSTRUCTION

_BATCH_SIZE = 32
_model_instance = None


def _auto_device() -> str:
    """Return "cuda" when a GPU is available, otherwise "cpu"."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_model() -> SentenceTransformer:
    """Load and cache the sentence-transformer model (once per process singleton)."""
    global _model_instance
    if _model_instance is None:
        _model_instance = SentenceTransformer(EMBEDDING_MODEL, device=_auto_device())
    return _model_instance


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks (no query prefix).

    Args:
        texts: Raw chunk texts.

    Returns:
        A list of 1024-dim, L2-normalized embedding vectors.
    """
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=_BATCH_SIZE,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


def embed_query(text: str) -> list[float]:
    """Embed a single search query with the required BGE instruction prefix.

    Args:
        text: The user's raw search query.

    Returns:
        A single 1024-dim, L2-normalized embedding vector.
    """
    model = get_model()
    vector = model.encode(
        QUERY_INSTRUCTION + text,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vector.tolist()

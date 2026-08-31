"""Central configuration for Talent Management Platform for Employee Performance and Career Growth (Milestone 1).

All settings are loaded from a ``.env`` file (via ``python-dotenv``) with
sensible defaults. No hardcoded absolute paths — every path is relative and
overridable through the environment so the project stays portable across
machines and later milestones.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load variables from a local .env file if present. Existing environment
# variables always win, so container/CI overrides keep working.
load_dotenv()


def _get_str(key: str, default: str) -> str:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- Embedding model -------------------------------------------------------
EMBEDDING_MODEL: str = _get_str("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM: int = 384
# BGE requires this instruction prefix on *queries only* (never on documents).
QUERY_INSTRUCTION: str = "Represent this sentence for searching relevant passages: "

# --- Vector store ----------------------------------------------------------
CHROMA_DB_PATH: str = _get_str("CHROMA_DB_PATH", "./chroma_db")
CHROMA_COLLECTION: str = _get_str("CHROMA_COLLECTION", "talent_sphere_docs")
CHROMA_HOST: str = _get_str("CHROMA_HOST", "localhost")
CHROMA_PORT: int = _get_int("CHROMA_PORT", 8001)

# --- Chunking --------------------------------------------------------------
CHUNK_SIZE: int = _get_int("CHUNK_SIZE", 800)
CHUNK_OVERLAP: int = _get_int("CHUNK_OVERLAP", 150)

# --- Retrieval -------------------------------------------------------------
TOP_K: int = _get_int("TOP_K", 5)

# --- Paths -----------------------------------------------------------------
DOCUMENTS_DIR: str = _get_str("DOCUMENTS_DIR", "./documents")

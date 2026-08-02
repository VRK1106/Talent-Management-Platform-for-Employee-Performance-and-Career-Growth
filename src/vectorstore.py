"""ChromaDB persistent vector store: add, query, de-dup, delete, and stats.

The collection is created with cosine space to match the normalized BGE
embeddings. File-level de-duplication is achieved by stamping every chunk's
metadata with the source file's sha256 hash; ingestion consults the set of
known hashes to skip files that were already indexed.
"""

from __future__ import annotations

import re
import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from src.config import CHROMA_COLLECTION, CHROMA_DB_PATH

_client_instance = None

def get_client() -> ClientAPI:
    """Return a cached, disk-persistent Chroma client."""
    global _client_instance
    if _client_instance is None:
        _client_instance = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return _client_instance


def get_collection() -> Collection:
    """Return (creating if needed) the cosine-space document collection."""
    client = get_client()
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def ingested_hashes() -> set[str]:
    """Return the set of file hashes already present in the index."""
    collection = get_collection()
    try:
        result = collection.get(include=["metadatas"])
    except Exception:  # noqa: BLE001 - empty/new collection
        return set()

    hashes: set[str] = set()
    for meta in result.get("metadatas") or []:
        file_hash = (meta or {}).get("file_hash")
        if file_hash:
            hashes.add(file_hash)
    return hashes


def add_chunks(chunks: list[dict], embeddings: list[list[float]], file_hash: str) -> int:
    """Upsert chunks and their embeddings into the collection.

    Args:
        chunks: Chunk dicts from :func:`src.ingest.chunk_pages`.
        embeddings: Parallel list of embedding vectors.
        file_hash: sha256 of the source file, stamped on every chunk for de-dup.

    Returns:
        The number of chunks added.
    """
    if not chunks:
        return 0

    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = []
    for chunk in chunks:
        meta = dict(chunk["metadata"])
        meta["file_hash"] = file_hash
        metadatas.append(meta)

    collection = get_collection()
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(ids)


def search(
    query_embedding: list[float],
    top_k: int,
    source_filters: list[str] | None = None,
    threshold: float = 0.0,
) -> list[dict]:
    """Run a cosine similarity search and return ranked results.

    Args:
        query_embedding: The query's embedding vector.
        top_k: Number of results to return.
        source_filters: Optional list of source filenames to restrict the search.
        threshold: Minimum similarity score (1 - distance) required.

    Returns:
        A list of ``{text, source, page, chunk_index, score}`` dicts sorted by
        descending similarity, where ``score = 1 - distance``.
    """
    collection = get_collection()
    total_count = collection.count()
    if total_count == 0:
        return []

    # Build where clause for metadata filters
    where_clause = None
    if source_filters:
        if len(source_filters) == 1:
            where_clause = {"source": source_filters[0]}
        elif len(source_filters) > 1:
            where_clause = {"$or": [{"source": src} for src in source_filters]}

    # Query for slightly more results to ensure we have top_k after thresholding
    query_k = min(max(top_k * 2, 20), total_count)

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=query_k,
        where=where_clause,
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[dict] = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        score = 1.0 - float(distance)
        if score >= threshold:
            hits.append(
                {
                    "text": text,
                    "source": meta.get("source", "unknown"),
                    "page": meta.get("page", "—"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": score,
                }
            )

    hits.sort(key=lambda hit: hit["score"], reverse=True)
    return hits[:top_k]


def delete_source(source_name: str) -> None:
    """Delete all chunks associated with a specific source document."""
    collection = get_collection()
    collection.delete(where={"source": source_name})


def get_source_chunks(source_name: str) -> list[dict]:
    """Retrieve all chunks for a specific source filename (for the chunk browser)."""
    collection = get_collection()
    try:
        result = collection.get(
            where={"source": source_name},
            include=["documents", "metadatas"],
        )
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        ids = result.get("ids") or []
        
        chunks = []
        for doc_id, text, meta in zip(ids, documents, metadatas):
            meta = meta or {}
            chunks.append({
                "id": doc_id,
                "text": text,
                "page": meta.get("page", "—"),
                "chunk_index": meta.get("chunk_index", 0)
            })
        # Sort chunks by page then by index
        chunks.sort(key=lambda x: (int(x["page"]) if isinstance(x["page"], int) or (isinstance(x["page"], str) and x["page"].isdigit()) else 0, x["chunk_index"]))
        return chunks
    except Exception:
        return []


def stats() -> dict:
    """Return index stats: total chunks, distinct source names, and detail cards."""
    collection = get_collection()
    total = collection.count()
    sources_dict: dict[str, int] = {}
    source_pages: dict[str, set[int]] = {}

    if total:
        try:
            result = collection.get(include=["metadatas"])
            for meta in result.get("metadatas") or []:
                meta = meta or {}
                source = meta.get("source")
                if source:
                    sources_dict[source] = sources_dict.get(source, 0) + 1
                    page = meta.get("page")
                    if page is not None:
                        if source not in source_pages:
                            source_pages[source] = set()
                        source_pages[source].add(page)
        except Exception:  # noqa: BLE001 - best-effort stats
            pass

    source_details = []
    for src in sorted(sources_dict.keys()):
        pages_set = source_pages.get(src, set())
        pages_count = len(pages_set)
        source_details.append(
            {
                "name": src,
                "chunks": sources_dict[src],
                "pages": pages_count if pages_count > 0 else 1,
            }
        )

    return {
        "total_chunks": total,
        "sources": len(sources_dict),
        "source_names": sorted(sources_dict.keys()),
        "source_details": source_details,
    }


def reset_collection() -> None:
    """Delete and recreate the collection (clears the whole index)."""
    client = get_client()
    try:
        client.delete_collection(CHROMA_COLLECTION)
    except Exception:  # noqa: BLE001 - already absent
        pass
    client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


_ephemeral_client_instance = None

def get_ephemeral_client() -> ClientAPI:
    """Return a cached, in-memory ephemeral Chroma client."""
    global _ephemeral_client_instance
    if _ephemeral_client_instance is None:
        _ephemeral_client_instance = chromadb.EphemeralClient()
    return _ephemeral_client_instance


def _sanitize_collection_name(session_id: str) -> str:
    """Sanitize the session_id to conform to Chroma collection naming constraints."""
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id)
    if not sanitized:
        sanitized = "session_collection"
    if not sanitized[0].isalnum():
        sanitized = 's_' + sanitized[1:]
    if not sanitized[-1].isalnum():
        sanitized = sanitized[:-1] + 'x'
    return sanitized[:63]


def get_ephemeral_collection(session_id: str) -> Collection:
    """Return (creating if needed) the session-scoped ephemeral collection."""
    client = get_ephemeral_client()
    coll_name = _sanitize_collection_name(f"ephemeral_{session_id}")
    return client.get_or_create_collection(
        name=coll_name,
        metadata={"hnsw:space": "cosine"},
    )


def add_ephemeral_chunks(session_id: str, chunks: list[dict], embeddings: list[list[float]], file_hash: str) -> int:
    """Upsert chunks and their embeddings into the session's ephemeral collection."""
    if not chunks:
        return 0

    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = []
    for chunk in chunks:
        meta = dict(chunk["metadata"])
        meta["file_hash"] = file_hash
        metadatas.append(meta)

    collection = get_ephemeral_collection(session_id)
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(ids)


def search_ephemeral(
    session_id: str,
    query_embedding: list[float],
    top_k: int,
    threshold: float = 0.0,
    source_filters: list[str] = None,
) -> list[dict]:
    """Run similarity search on the ephemeral collection and return sorted results."""
    collection = get_ephemeral_collection(session_id)
    total_count = collection.count()
    if total_count == 0:
        return []

    # Build where clause for metadata filters
    where_clause = None
    if source_filters is not None:
        if len(source_filters) == 1:
            where_clause = {"source": source_filters[0]}
        elif len(source_filters) > 1:
            where_clause = {"$or": [{"source": src} for src in source_filters]}
        else:
            # If an empty list of files is passed for this week, return nothing
            return []

    query_k = min(max(top_k * 2, 20), total_count)

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=query_k,
        where=where_clause,
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[dict] = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        score = 1.0 - float(distance)
        if score >= threshold:
            hits.append(
                {
                    "text": text,
                    "source": meta.get("source", "Uploaded Document"),
                    "page": meta.get("page", "—"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "score": score,
                }
            )

    hits.sort(key=lambda hit: hit["score"], reverse=True)
    return hits[:top_k]


def delete_ephemeral_collection(session_id: str) -> None:
    """Explicitly delete the ephemeral session-scoped collection from memory."""
    client = get_ephemeral_client()
    coll_name = _sanitize_collection_name(f"ephemeral_{session_id}")
    try:
        client.delete_collection(coll_name)
    except Exception:
        pass


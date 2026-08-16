"""ChromaDB persistent vector store: add, query, de-dup, delete, and stats.

The collection is created with cosine space to match the normalized BGE
embeddings. File-level de-duplication is achieved by stamping every chunk's
metadata with the source file's sha256 hash; ingestion consults the set of
known hashes to skip files that were already indexed.
"""

from __future__ import annotations

import re
import sys
try:
    import pysqlite3
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from src.config import CHROMA_COLLECTION, CHROMA_DB_PATH

import threading
_chroma_lock = threading.Lock()
_client_instance = None

def get_client() -> ClientAPI:
    """Return a cached, disk-persistent Chroma client with self-healing recovery."""
    global _client_instance
    with _chroma_lock:
        if _client_instance is None:
            try:
                _client_instance = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            except Exception as e:
                error_msg = str(e).lower()
                if "sqlite" in error_msg and "version" in error_msg:
                    print(f"[ERROR] ChromaDB requires a newer SQLite3 version. Install pysqlite3-binary. Error: {e}")
                    raise
                print(f"[WARN] ChromaDB init error: {e}. Executing self-healing index reset...")
                import shutil, time, pathlib
                backup_dir = f"{CHROMA_DB_PATH}_corrupt_{int(time.time())}"
                try:
                    if pathlib.Path(CHROMA_DB_PATH).exists():
                        shutil.move(CHROMA_DB_PATH, backup_dir)
                except Exception as ex:
                    print(f"[WARN] Failed to move corrupt DB dir: {ex}")
                _client_instance = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        return _client_instance


def get_collection() -> Collection | None:
    """Return (creating if needed) the cosine-space document collection."""
    global _client_instance
    try:
        client = get_client()
        return client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as e:
        print(f"[WARN] Error accessing collection: {e}. Resetting client...")
        _client_instance = None
        try:
            client = get_client()
            return client.get_or_create_collection(
                name=CHROMA_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as ex:
            print(f"[ERROR] Failed to obtain vector collection: {ex}")
            return None


def ingested_hashes() -> set[str]:
    """Return the set of file hashes already present in DOCUMENTS_DIR."""
    hashes: set[str] = set()
    try:
        from pathlib import Path
        from src.config import DOCUMENTS_DIR
        from src.ingest import file_hash
        doc_dir = Path(DOCUMENTS_DIR)
        if doc_dir.exists():
            for f in doc_dir.iterdir():
                if f.is_file() and not f.name.startswith('.') and not f.name.startswith('Custom_'):
                    try:
                        hashes.add(file_hash(f.read_bytes()))
                    except Exception:
                        pass
    except Exception as ex:
        print(f"Error reading ingested hashes: {ex}")
    return hashes


def add_chunks(chunks: list[dict], embeddings: list[list[float]], file_hash: str) -> int:
    """Upsert chunks and their embeddings into the collection."""
    if not chunks:
        return 0

    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = []
    for chunk in chunks:
        meta = dict(chunk["metadata"])
        meta["file_hash"] = file_hash
        metadatas.append(meta)

    with _chroma_lock:
        collection = get_collection()
        if not collection:
            return 0
        try:
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
            invalidate_stats_cache()
            return len(ids)
        except Exception as e:
            print(f"[ERROR] Failed to upsert chunks: {e}")
            return 0


def search(
    query_embedding: list[float],
    top_k: int,
    source_filters: list[str] | None = None,
    threshold: float = 0.0,
) -> list[dict]:
    """Run a cosine similarity search and return ranked results."""
    with _chroma_lock:
        collection = get_collection()
        if not collection:
            return []
        try:
            total_count = collection.count()
            if total_count == 0:
                return []

            where_clause = None
            if source_filters:
                if len(source_filters) == 1:
                    where_clause = {"source": source_filters[0]}
                elif len(source_filters) > 1:
                    where_clause = {"$or": [{"source": src} for src in source_filters]}

            query_k = min(max(top_k * 2, 20), total_count)

            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=query_k,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            print(f"[ERROR] Vector search failed: {e}")
            return []

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


def delete_source(source_name: str, file_hash: str | None = None) -> None:
    """Delete all chunks associated with a specific source document or file_hash by exact IDs."""
    with _chroma_lock:
        collection = get_collection()
        if not collection:
            return
        import urllib.parse
        names_to_delete = {source_name, urllib.parse.unquote(source_name), urllib.parse.quote(source_name)}
        
        hashes_to_delete = set()
        if file_hash:
            hashes_to_delete.add(file_hash)
            
        for name in names_to_delete:
            try:
                res = collection.get(where={"source": name}, include=["metadatas"])
                ids = res.get("ids") or []
                if ids:
                    for meta in res.get("metadatas") or []:
                        meta = meta or {}
                        h = meta.get("file_hash")
                        if h:
                            hashes_to_delete.add(h)
                    collection.delete(ids=ids)
            except Exception as e:
                print(f"Error querying/deleting metadata for source '{name}': {e}")

        # Delete any remaining matching file_hashes by exact IDs
        for h in hashes_to_delete:
            try:
                res = collection.get(where={"file_hash": h}, include=["metadatas"])
                ids = res.get("ids") or []
                if ids:
                    collection.delete(ids=ids)
            except Exception as e:
                print(f"Error deleting file_hash '{h}': {e}")

        invalidate_stats_cache()


def get_source_chunks(source_name: str) -> list[dict]:
    """Retrieve all chunks for a specific source filename (for the chunk browser)."""
    with _chroma_lock:
        if _client_instance is None:
            return []
        collection = get_collection()
        if not collection:
            return []
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


_stats_cache = None
_stats_cache_time = 0.0

def invalidate_stats_cache():
    global _stats_cache_time
    _stats_cache_time = 0.0

def _fetch_stats_raw() -> dict:
    with _chroma_lock:
        collection = get_collection()
        if not collection:
            return {"total_chunks": 0, "sources": 0, "source_names": [], "source_details": []}
        total = collection.count()
        if not total:
            return {"total_chunks": 0, "sources": 0, "source_names": [], "source_details": []}

        from pathlib import Path
        from src.config import DOCUMENTS_DIR
        doc_dir = Path(DOCUMENTS_DIR)
        doc_names = []
        if doc_dir.exists():
            for f in doc_dir.iterdir():
                if f.is_file() and not f.name.startswith('.') and not f.name.startswith('Custom_'):
                    doc_names.append(f.name)
        doc_names = sorted(doc_names)

        sources_dict: dict[str, int] = {}
        source_pages: dict[str, set[int]] = {}
        
        for src in doc_names:
            try:
                result = collection.get(where={"source": src}, include=["metadatas"])
                metadatas = result.get("metadatas") or []
                if metadatas:
                    sources_dict[src] = len(metadatas)
                    pages_set = set()
                    for meta in metadatas:
                        meta = meta or {}
                        page = meta.get("page")
                        if page is not None:
                            pages_set.add(page)
                    source_pages[src] = pages_set
            except Exception as e:
                print(f"Error getting stats for document {src}: {e}")

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


def stats() -> dict:
    """Return index stats safely and instantly without blocking page load."""
    global _stats_cache, _stats_cache_time
    import time
    now = time.time()
    if _stats_cache is not None and (now - _stats_cache_time) < 30.0:
        return _stats_cache

    fallback = {
        "total_chunks": 0,
        "sources": 0,
        "source_names": [],
        "source_details": [],
    }

    try:
        if _client_instance is None:
            _stats_cache = fallback
            _stats_cache_time = now
            return fallback

        res = _fetch_stats_raw()
        _stats_cache = res
        _stats_cache_time = now
        return res
    except Exception as e:
        print(f"[WARN] Vectorstore stats fallback: {e}")
        return _stats_cache if _stats_cache is not None else fallback


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


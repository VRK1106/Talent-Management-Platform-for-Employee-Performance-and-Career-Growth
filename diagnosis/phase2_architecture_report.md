# Phase 2 Architecture Review: Bottlenecks & Vulnerabilities

This report outlines specific architectural bottlenecks within the ChromaDB vector store, BGE embedding utilities, and Groq LLM integration.

## 1. Heavy Synchronous Operations (Blocking ASGI Worker Thread)

The following functions perform heavy synchronous operations that threaten to block the ASGI worker thread, primarily due to tensor allocations or Hugging Face model loading in the main thread:

*   **`get_model`** (in `src/embeddings.py`): Synchronously loads the `SentenceTransformer(EMBEDDING_MODEL)` model and allocates tensors in PyTorch. The first request that triggers this, or the module load itself, will freeze the worker thread while the multi-gigabyte model is loaded into memory.

## 2. Missing LLM HTTP 429 Rate-Limit Fallbacks / Error Handling

The HTTP request logic in the following functions fails to implement proper timeout fallbacks, exponential backoffs, or error handling specifically for HTTP 429 Rate Limits from the LLM provider:

*   **`generate_text`** (in `src/llm.py`)
*   **`generate_rag_answer`** (in `src/llm.py`)
*   **`generate_chat_answer`** (in `src/llm.py`)
*   **`generate_study_plan`** (in `src/llm.py`)
*   **`analyze_proctor_image`** (in `src/llm.py`): Handles 400/403/404 HTTP errors but completely lacks 429 rate limit handling.
*   **`transcribe_audio_whisper`** (in `src/llm.py`)

## 3. Silently Ingesting Duplicate Vector Text Arrays

The vector chunking logic and file hashing allows duplicate text arrays to be silently ingested into ChromaDB in the following functions:

*   **`chunk_pages`** (in `src/ingest.py`): Generates chunk IDs sequentially based on the `source_name` (filename) and page number (e.g., `f"{source_name}::p{page_number}::c{running_index}"`). It fails to hash the actual chunk text, meaning identical content uploaded under a different filename generates unique IDs.
*   **`add_chunks`** (in `src/vectorstore.py`): Blindly calls `collection.upsert()` using those uniquely generated, filename-derived IDs without querying `file_hash` to detect if the text content already exists in the vector store.

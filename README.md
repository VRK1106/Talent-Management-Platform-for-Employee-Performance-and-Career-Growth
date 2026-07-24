# Talent Sphere Elevate

An advanced Streamlit dashboard for document ingestion, semantic search, and Retrieval-Augmented Generation (RAG).

## Key Features

- **Document Ingestion**: Extract text from PDF documents with OCR fallback, chunk texts, and index them.
- **Semantic Search Lab**: Run semantic queries using Sentence-Transformers (BGE-Large) and filter/inspect matching passages.
- **Local Qwen RAG**: Get contextualized answers powered by local Qwen LLM connector via Ollama API.
- **Rich Aesthetics**: Responsive dark glassmorphism and light slate stylesheet for a modern user experience.

## Project Structure

```
Talent-Sphere-Elevate/
├── app.py                     # Entry point for the Streamlit dashboard
├── requirements.txt           # Python package dependencies
├── .env                       # Environment configuration
├── assets/                    # Styling and visual assets
│   └── styles.css             # Unified dark glassmorphism & light slate stylesheet
├── pages/                     # Streamlit multi-page interface files
│   ├── 1_📥_Ingest.py         # Document Ingestion Pipeline & Catalog
│   └── 2_🔍_Search.py         # Semantic Search Lab & Qwen RAG
├── src/                       # Backend architecture modules
│   ├── config.py              # Constants, model paths, and Top-K config
│   ├── embeddings.py          # Sentence-transformers (BGE-Large) interface
│   ├── ingest.py              # PDF extraction, OCR fallback, and text chunking
│   ├── llm.py                 # Local Qwen LLM connector via Ollama API
│   ├── ui.py                  # HTML cards, highlighter, and sidebar template
│   └── vectorstore.py         # ChromaDB operations, filters, stats, and deletions
├── documents/                 # Folder containing uploaded raw PDF files
└── chroma_db/                 # Persistent ChromaDB vector index database directory
```

## Setup & Running

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Environment Variables**:
   Configure the `.env` file based on `.env.example`.
3. **Run App**:
   ```bash
   streamlit run app.py
   ```

import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["CHROMA_TELEMETRY_DISABLED"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

from src.embeddings import embed_query
from src.vectorstore import search
from src.llm import generate_chat_answer_stream

print("1. Testing PyTorch Embedding...")
vec = embed_query("test query")
print(f"Embedding successful: vector length {len(vec)}")

print("2. Testing ChromaDB retrieval...")
results = search(vec, top_k=2)
print(f"Retrieved {len(results)} chunks from vector store.")

print("3. Testing Groq Streaming...")
for chunk in generate_chat_answer_stream("Hello, testing connection.", model="qwen/qwen3.8-27b"):
    print(chunk, end="", flush=True)
print("\n--- TEST COMPLETE ---")
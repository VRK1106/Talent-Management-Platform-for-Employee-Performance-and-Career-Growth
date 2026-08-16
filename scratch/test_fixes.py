import os
import sys

# Load environment
from dotenv import load_dotenv
load_dotenv()

print("--- Testing ChromaDB ---")
try:
    from src.vectorstore import get_client, stats
    client = get_client()
    print("ChromaDB Client initialized.")
    st = stats()
    print("Stats:", st)
except Exception as e:
    print("ChromaDB Error:", e)

print("\n--- Testing Groq ---")
try:
    from src.llm import generate_chat_answer_stream
    print("Groq stream test:")
    for chunk in generate_chat_answer_stream("Hello, say 'Test successful'", "llama-3.1-8b-instant"):
        print(chunk, end="", flush=True)
    print("\nGroq test done.")
except Exception as e:
    print("Groq Error:", e)

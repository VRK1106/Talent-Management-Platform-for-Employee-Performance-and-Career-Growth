import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GROQ_API_KEY")

response = requests.get(
    "https://api.groq.com/openai/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}
)

try:
    models = response.json().get("data", [])
    print("Models you can actually use:")
    for m in models:
        print(f" - {m['id']}")
except Exception as e:
    print("Failed to fetch models:", response.text)
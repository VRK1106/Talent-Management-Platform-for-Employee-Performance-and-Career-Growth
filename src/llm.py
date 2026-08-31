"""Groq LLM connector.

Communicates with the Groq API at https://api.groq.com.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import time
import random
from typing import Any
from dotenv import load_dotenv

def clean_json_response(raw_resp: str) -> str:
    """Strip markdown backticks and extract JSON string content."""
    resp = raw_resp.strip()
    if resp.startswith("```"):
        match = re.match(r"^```(?:json)?\s*", resp)
        if match:
            resp = resp[match.end():]
        if resp.endswith("```"):
            resp = resp[:-3]
    resp = resp.strip()
    start_obj = resp.find('{')
    start_arr = resp.find('[')
    
    if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
        end_obj = resp.rfind('}')
        if end_obj > start_obj:
            return resp[start_obj:end_obj+1].strip()
    elif start_arr != -1:
        end_arr = resp.rfind(']')
        if end_arr > start_arr:
            return resp[start_arr:end_arr+1].strip()
            
    return resp

# Load environment variables
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Standard headers to prevent 403 Forbidden blocks from WAFs
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def execute_with_backoff(req, max_retries=5, base_delay=1.0):
    """Executes a urllib Request with Retry-After awareness and exponential backoff."""
    for attempt in range(max_retries):
        try:
            return urllib.request.urlopen(req, timeout=180.0)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Groq API rate limit persists after {max_retries} retries.")
                
                # 1. Prefer Groq's explicit Retry-After header
                retry_after = e.headers.get("Retry-After")
                
                if retry_after and retry_after.replace('.', '', 1).isdigit():
                    wait_time = float(retry_after)
                else:
                    # 2. Fall back to exponential backoff with jitter
                    exponential_delay = base_delay * (2 ** attempt)
                    wait_time = exponential_delay * (0.5 + random.random() * 0.5)
                
                print(f"[WARN] Groq 429 Rate Limit. Retrying in {wait_time:.1f}s (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            
            # Retry on 5xx Server Errors
            elif e.code >= 500:
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))
                continue
            
            # Fail immediately on 400, 401, 403, 404
            raise

PRIMARY_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "groq/compound"

_groq_client = None

def get_groq_client():
    """Return global singleton Groq SDK client, instantiated once at app startup."""
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY)
        except Exception as e:
            print(f"[WARN] Failed to initialize Groq client: {e}")
    return _groq_client


def generate_text(prompt: str) -> str:
    """Generate completion from prompt using PRIMARY_MODEL with seamless failover to FALLBACK_MODEL."""
    client = get_groq_client()
    if not client:
        raise RuntimeError("Groq client is not initialized or GROQ_API_KEY is missing.")
    try:
        # Attempt primary execution
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=PRIMARY_MODEL,
            max_tokens=1024
        )
        return response.choices[0].message.content
        
    except Exception as e:
        error_msg = str(e)
        # Only fall back if the model itself is missing or dead
        if "model_not_found" in error_msg or "model_decommissioned" in error_msg:
            print(f"[Warning] {PRIMARY_MODEL} unavailable. Falling back to {FALLBACK_MODEL}.")
            try:
                fallback_resp = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=FALLBACK_MODEL,
                    max_tokens=1024
                )
                return fallback_resp.choices[0].message.content
            except Exception as fallback_err:
                print(f"[Fatal] Fallback model also failed: {fallback_err}")
                raise
        else:
            # If the error is a rate limit or bad prompt, don't waste time falling back
            print(f"[Error] Groq API execution failed: {error_msg}")
            raise


def list_local_models() -> list[str]:
    """Fetch the list of model names currently available in Groq API.
    
    If the API key is not set or request fails, falls back to a list of standard Groq models.
    """
    fallback_models = [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        "qwen/qwen3.8-27b",
        "groq/compound-mini"
    ]
    if not GROQ_API_KEY:
        return fallback_models
        
    try:
        url = "https://api.groq.com/openai/v1/models"
        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"
        
        req = urllib.request.Request(
            url,
            headers=req_headers,
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["id"] for m in data.get("data", [])]
            # Keep only common text models (filter out audio/whisper models)
            text_models = [
                m for m in models 
                if "whisper" not in m.lower() and "audio" not in m.lower() and "guard" not in m.lower()
            ]
            if text_models:
                # Ensure our fallbacks are prioritized at the top of the list if returned by the API
                prioritized = [m for m in fallback_models if m in text_models]
                others = [m for m in text_models if m not in fallback_models]
                return prioritized + others
            return fallback_models
    except Exception:
        return fallback_models


def generate_rag_answer(query: str, chunks: list[dict[str, Any]], model_name: str) -> str:
    """Generate an answer using retrieved document contexts via Groq API."""
    if not chunks:
        return "No context available to answer the query. Please upload documents first."

    if not GROQ_API_KEY:
        return "Groq API Key is not configured. Please add GROQ_API_KEY to your .env file."

    # Construct context block
    context_parts = []
    for i, chunk in enumerate(chunks, start=1):
        source = chunk.get("source", "Unknown Source")
        page = chunk.get("page", "?")
        text = chunk.get("text", "")
        context_parts.append(f"[{i}] Source: {source} (Page {page})\nContent: {text}")

    context_str = "\n\n".join(context_parts)

    system_instruction = (
        "You are an expert AI assistant for 'Talent Management Platform for Employee Performance and Career Growth', a smart document retrieval and QA platform. "
        "Your task is to answer the user's query truthfully using ONLY the provided document context below. "
        "If the answer cannot be found in the context, state that you do not know based on the provided documents. "
        "Do not make up facts. "
        "Do NOT include any code blocks, programming snippets, or code examples in your response unless the user explicitly asks for code or programming implementation.\n\n"
        "Cite your sources using bracketed numbers corresponding to the context passages (e.g. [1], [2]) where appropriate."
    )

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"--- CONTEXT PASSAGES ---\n{context_str}\n\n--- USER QUERY ---\n{query}"}
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
            "top_p": 0.9,
        }

        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )

        with execute_with_backoff(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.URLError as e:
        return f"Error connecting to Groq API: {e.reason}."
    except Exception as e:
        return f"An unexpected error occurred while generating answer: {e}"


def generate_chat_answer(prompt: str, model_name: str, system_instruction: str | None = None) -> str:
    """Generate a general model completion from a prompt via Groq API with rate limit fallbacks."""
    if not GROQ_API_KEY:
        return "Groq API Key is not configured. Please add GROQ_API_KEY to your .env file."

    candidate_models = [model_name, "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "groq/compound"]
    models_to_try = []
    for m in candidate_models:
        if m and m not in models_to_try:
            models_to_try.append(m)

    last_error = ""

    for attempt_model in models_to_try:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": prompt})
                
            payload = {
                "model": attempt_model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1024,
                "top_p": 0.9,
            }

            req_headers = HEADERS.copy()
            req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=req_headers,
                method="POST",
            )

            with execute_with_backoff(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            last_error = f"Error connecting to Groq API: {e.reason}."
            if e.code == 429:
                import time
                time.sleep(0.5)
                continue
            return last_error
        except urllib.error.URLError as e:
            last_error = f"Error connecting to Groq API: {e.reason}."
            continue
        except Exception as e:
            last_error = f"An unexpected error occurred: {e}"
            continue

    return last_error if last_error else "Error connecting to Groq API."


def generate_rag_answer_stream(query: str, chunks: list[dict[str, Any]], model_name: str):
    """Yield chunks of text generated using retrieved document contexts via Groq API."""
    if not chunks:
        yield "No context available to answer the query. Please upload documents first."
        return

    if not GROQ_API_KEY:
        yield "Groq API Key is not configured. Please add GROQ_API_KEY to your .env file."
        return

    context_parts = []
    for i, chunk in enumerate(chunks, start=1):
        source = chunk.get("source", "Unknown Source")
        page = chunk.get("page", "?")
        text = chunk.get("text", "")
        context_parts.append(f"[{i}] Source: {source} (Page {page})\nContent: {text}")

    context_str = "\n\n".join(context_parts)

    system_instruction = (
        "You are an expert AI assistant for 'Talent Management Platform for Employee Performance and Career Growth', a smart document retrieval and QA platform. "
        "Your task is to answer the user's query truthfully using ONLY the provided document context below. "
        "If the answer cannot be found in the context, state that you do not know based on the provided documents. "
        "Do not make up facts. "
        "Do NOT include any code blocks, programming snippets, or code examples in your response unless the user explicitly asks for code or programming implementation.\n\n"
        "Cite your sources using bracketed numbers corresponding to the context passages (e.g. [1], [2]) where appropriate.\n"
        "When presenting tabular or structured list data, always format it as a markdown table.\n"
        "When presenting sequential, process, workflow, or step-by-step data, always format/render it as a Mermaid.js flowchart (enclosed in a '```mermaid' code block, e.g. using 'graph TD' or 'flowchart LR').\n"
        "CRITICAL FOR MERMAID: To avoid syntax errors, you MUST wrap all node labels in double quotes (e.g., A[\"User Request\"] --> B[\"Process File\"]). Do not use parentheses or special characters outside of quotes. Keep the Mermaid code block extremely simple and standard."
    )

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"--- CONTEXT PASSAGES ---\n{context_str}\n\n--- USER QUERY ---\n{query}"}
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
            "top_p": 0.9,
            "stream": True
        }

        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )

        with execute_with_backoff(req) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_content = line_str[6:]
                    if data_content == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data_content)
                        delta = chunk_data["choices"][0].get("delta", {})
                        
                        # 1. Capture standard text or reasoning tokens
                        token = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or ""
                        
                        # 2. Intercept agentic tool calls from gpt-oss-120b
                        tool_calls = delta.get("tool_calls")
                        if tool_calls:
                            for tool in tool_calls:
                                if "function" in tool and "name" in tool["function"]:
                                    token += f"\n\n> 🔍 **Agent Action:** Executing `{tool['function']['name']}`...\n\n"
                                    
                        if token:
                            yield token
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        if e.code == 429:
            yield "Error: Groq API Rate Limit Exceeded (429). Please wait a moment and try again."
        else:
            yield f"Error connecting to Groq API: HTTP {e.code} {e.reason}."
    except urllib.error.URLError as e:
        yield f"Error connecting to Groq API: {e.reason}."
    except Exception as e:
        yield f"An unexpected error occurred while generating answer: {e}"


def generate_chat_answer_stream(prompt: str, model_name: str, system_instruction: str | None = None):
    """Yield chunks of text generated from a prompt via Groq API with reasoning and content extraction."""
    if not GROQ_API_KEY:
        yield "Groq API Key is not configured. Please add GROQ_API_KEY to your .env file."
        return

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
            
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "stream": True
        }

        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )

        with execute_with_backoff(req) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_content = line_str[6:]
                    if data_content == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data_content)
                        delta = chunk_data["choices"][0].get("delta", {})
                        
                        # 1. Capture standard text or reasoning tokens
                        token = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or ""
                        
                        # 2. Intercept agentic tool calls from gpt-oss-120b
                        tool_calls = delta.get("tool_calls")
                        if tool_calls:
                            for tool in tool_calls:
                                if "function" in tool and "name" in tool["function"]:
                                    token += f"\n\n> 🔍 **Agent Action:** Executing `{tool['function']['name']}`...\n\n"
                                    
                        if token:
                            yield token
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        if e.code == 429:
            yield "Error: Groq API Rate Limit Exceeded (429). Please wait a moment and try again."
        else:
            yield f"Error connecting to Groq API: HTTP {e.code} {e.reason}."
    except urllib.error.URLError as e:
        yield f"Error connecting to Groq API: {e.reason}."
    except Exception as e:
        yield f"An unexpected error occurred: {e}"


def _analyze_proctor_image_local(image_base64: str) -> str:
    """Analyze a base64 encoded JPEG image locally using Ollama's Moondream model."""
    try:
        # Strip data URL prefix if present
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]
            
        url = "http://localhost:11434/api/chat"
        payload = {
            "model": "moondream:latest",
            "messages": [
                {
                    "role": "user",
                    "content": "Does this image show a phone, a second person, or is the student absent? Answer in one word: none / phone / second_person / absent.",
                    "images": [image_base64]
                }
            ],
            "stream": False
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data.get("message", {}).get("content", "").strip().lower()
            print(f"[PROCTOR OLLAMA] Local Moondream response: '{answer}'")
            for label in ["phone", "second_person", "absent", "none"]:
                if label in answer:
                    return label
            return "none"
    except Exception as e:
        print(f"[PROCTOR OLLAMA] Error in local vision analysis: {e}")
        return "none"


def analyze_proctor_image(image_base64: str) -> str:
    """Analyze a base64 encoded JPEG image. Try local Ollama Moondream first, fallback to Groq."""
    # 1. Try local Ollama Moondream if available
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(('localhost', 11434))
        s.close()
        
        # Ollama is running, execute local analysis
        ollama_res = _analyze_proctor_image_local(image_base64)
        if ollama_res != "none":
            return ollama_res
    except Exception:
        pass
        
    # 2. Fallback to Groq API
    if not GROQ_API_KEY:
        return "none"
        
    try:
        # Strip data URL prefix if present
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]
            
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "qwen/qwen3.6-27b",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Does this image show a phone, a second person, or the student absent from frame? Answer only: none / phone / second_person / absent."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.0,
            "max_tokens": 10
        }
        
        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )
        
        with execute_with_backoff(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data["choices"][0]["message"]["content"].strip().lower()
            for label in ["phone", "second_person", "absent", "none"]:
                if label in answer:
                    return label
            return "none"
    except urllib.error.HTTPError as e:
        if e.code in [400, 403, 404]:
            print(f"[PROCTOR VISION] Groq vision model not available or decommissioned ({e.code} {e.reason}). Bypassing AI validation.")
        else:
            print(f"[PROCTOR VISION] HTTP error during analysis: {e.code} {e.reason}")
        return "none"
    except Exception as e:
        print(f"[PROCTOR VISION] Error in Groq vision analysis: {e}")
        return "none"


def transcribe_audio_whisper(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Transcribe audio using Groq Whisper Large V3 (free-tier eligible).

    Args:
        audio_bytes: Raw audio bytes from the browser MediaRecorder.
        mime_type:   MIME type reported by the browser (e.g. 'audio/webm',
                     'audio/ogg', 'audio/mp4').  Extension is inferred
                     from this so Groq can detect the codec.

    Returns:
        Transcribed text string.

    Raises:
        RuntimeError: if GROQ_API_KEY is missing or the API call fails.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured. Voice transcription is unavailable.")

    import tempfile
    import pathlib

    # Map MIME type -> file extension that Groq accepts
    _MIME_TO_EXT: dict[str, str] = {
        "audio/webm": "webm",
        "audio/ogg":  "ogg",
        "audio/ogg;codecs=opus": "ogg",
        "audio/mp4":  "mp4",
        "audio/mpeg": "mp3",
        "audio/wav":  "wav",
        "audio/x-wav": "wav",
        "audio/flac": "flac",
    }
    base_mime = mime_type.split(";")[0].strip().lower()
    ext = _MIME_TO_EXT.get(base_mime, "webm")

    try:
        from groq import Groq  # type: ignore
        client = Groq(api_key=GROQ_API_KEY)

        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = pathlib.Path(tmp.name)

        try:
            with open(tmp_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(tmp_path.name, audio_file),
                    model="whisper-large-v3",
                    response_format="text",
                    language="en",
                    temperature=0.0,
                )
            return str(transcription).strip()
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass

    except ImportError:
        raise RuntimeError(
            "The 'groq' Python package is required for voice transcription. Run: pip install groq"
        )
    except Exception as exc:
        raise RuntimeError(f"Whisper transcription failed: {exc}") from exc


def generate_ephemeral_rag_answer_stream(query: str, chunks: list[dict[str, Any]], model_name: str, is_admin: bool = False):
    """Yield chunks of text generated using retrieved document contexts, strictly retrieval-only (no fallback) unless is_admin is True."""
    if not chunks:
        if is_admin:
            system_prompt = (
                "You are a helpful learning coach. Provide clear, professional explanations or advice. "
                "You may fall back to your general model knowledge because no document context is currently available."
            )
            yield from generate_chat_answer_stream(query, model_name, system_prompt)
        else:
            yield "I am sorry, but the answer to your question is not present in the provided document."
        return

    if not GROQ_API_KEY:
        yield "Error: Groq API Key is not configured. Please set GROQ_API_KEY in your environment."
        return

    context_parts = []
    for i, chunk in enumerate(chunks, start=1):
        page = chunk.get("page", "?")
        text = chunk.get("text", "")
        context_parts.append(f"Content from page {page}:\n{text}")

    context_str = "\n\n".join(context_parts)

    if is_admin:
        system_instruction = (
            "You are a helpful learning coach for 'Talent Management Platform for Employee Performance and Career Growth'. Answer the user's query. "
            "Use the provided context passages below to guide your answer, but you are allowed to supplement "
            "it or fall back to your general model knowledge if the context is insufficient or if the query requires it.\n"
            "Cite your sources using bracketed numbers corresponding to the context passages (e.g. [1], [2]) where appropriate.\n"
            "When presenting tabular or structured list data, always format it as a markdown table.\n"
            "When presenting sequential, process, workflow, or step-by-step data, always format/render it as a Mermaid.js flowchart (enclosed in a '```mermaid' code block, e.g. using 'graph TD' or 'flowchart LR').\n"
            "CRITICAL FOR MERMAID: To avoid syntax errors, you MUST wrap all node labels in double quotes (e.g., A[\"User Request\"] --> B[\"Process File\"]). Do not use parentheses or special characters outside of quotes. Keep the Mermaid code block extremely simple and standard."
        )
    else:
        system_instruction = (
            "You are a strict retrieval-only Q&A assistant for 'Talent Management Platform for Employee Performance and Career Growth'. Your task is to answer the user's query using ONLY the provided document context below.\n"
            "If the answer cannot be found in the context, you MUST respond exactly with: 'I am sorry, but the answer to your question is not present in the provided document.'\n"
            "Do NOT make up facts, and do NOT fall back to your general model knowledge under any circumstances. Keep your answer factual, direct, and fully based on the context.\n"
            "Cite your sources using bracketed numbers corresponding to the context passages (e.g. [1], [2]) where appropriate.\n"
            "When presenting tabular or structured list data, always format it as a markdown table.\n"
            "When presenting sequential, process, workflow, or step-by-step data, always format/render it as a Mermaid.js flowchart (enclosed in a '```mermaid' code block, e.g. using 'graph TD' or 'flowchart LR').\n"
            "CRITICAL FOR MERMAID: To avoid syntax errors, you MUST wrap all node labels in double quotes (e.g., A[\"User Request\"] --> B[\"Process File\"]). Do not use parentheses or special characters outside of quotes. Keep the Mermaid code block extremely simple and standard."
        )

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"--- CONTEXT PASSAGES ---\n{context_str}\n\n--- USER QUERY ---\n{query}"}
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
            "top_p": 0.9,
            "stream": True
        }

        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )

        with execute_with_backoff(req) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_content = line_str[6:]
                    if data_content == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data_content)
                        delta = chunk_data["choices"][0].get("delta", {})
                        
                        # 1. Capture standard text or reasoning tokens
                        token = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or ""
                        
                        # 2. Intercept agentic tool calls from gpt-oss-120b
                        tool_calls = delta.get("tool_calls")
                        if tool_calls:
                            for tool in tool_calls:
                                if "function" in tool and "name" in tool["function"]:
                                    token += f"\n\n> 🔍 **Agent Action:** Executing `{tool['function']['name']}`...\n\n"
                                    
                        if token:
                            yield token
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        if e.code == 429:
            yield "Error: Groq API Rate Limit Exceeded (429). Please wait a moment and try again."
        else:
            yield f"Error connecting to Groq API: HTTP {e.code} {e.reason}."
    except urllib.error.URLError as e:
        yield f"Error: Connecting to Groq API failed: {e.reason}."
    except Exception as e:
        yield f"Error: An unexpected error occurred while generating answer: {e}"


def generate_study_plan(prompt: str, domain: str, week_number: int, model_name: str) -> dict:
    """Generate a weekly study plan using Groq LLM and return it as a dictionary."""
    if not GROQ_API_KEY:
        return {"error": "Groq API Key is not configured."}
        
    system_instruction = (
        "You are an expert technical curriculum designer for 'Talent Management Platform for Employee Performance and Career Growth'. "
        "Your task is to generate a structured 4-day learning sprint and a Day 6 mock interview prompt based on the user's request. "
        "You MUST return ONLY a valid, parseable JSON object with NO markdown formatting, NO backticks, and NO surrounding text. "
        "The JSON keys MUST be exactly:\n"
        "- 'title': A short, professional title for the week.\n"
        "- 'day1': List of 2-3 specific study tasks.\n"
        "- 'day2': List of 2-3 specific study tasks.\n"
        "- 'day3': List of 2-3 specific study tasks.\n"
        "- 'day4': List of 2-3 specific study tasks.\n"
        "- 'day6_prompt': A scenario/question for the Day 6 mock voice interview.\n\n"
        "Example JSON output:\n"
        "{\n"
        "  \"title\": \"Advanced Java Spring Boot\",\n"
        "  \"day1\": [\"Study JPA annotations\", \"Upload java_spring_docs.pdf\"],\n"
        "  \"day2\": [\"Practice building CRUD REST controllers\", \"Ask AI Coach about transactions\"],\n"
        "  \"day3\": [\"Complete Spring Boot exam\", \"Analyze incorrect responses\"],\n"
        "  \"day4\": [\"Run performance benchmarks\", \"Checklist audit\"],\n"
        "  \"day6_prompt\": \"Explain how Spring manages transaction propagation levels and rollbacks.\"\n"
        "}"
    )
    
    user_prompt = f"Create a Week {week_number} Agile study plan for the domain '{domain}' with details: {prompt}"
    
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        
        req_headers = HEADERS.copy()
        req_headers["Authorization"] = f"Bearer {GROQ_API_KEY}"
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=req_headers,
            method="POST"
        )
        
        with execute_with_backoff(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            # Parse the JSON string
            return json.loads(content)
    except Exception as e:
        print(f"Error generating study plan: {e}")
        return {"error": f"Failed to generate study plan: {str(e)}"}


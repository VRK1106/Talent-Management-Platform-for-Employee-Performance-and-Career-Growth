import os
import re
import hashlib
import asyncio
import tempfile
from pathlib import Path
from src.vectorstore import get_source_chunks
from src.llm import GROQ_API_KEY
import edge_tts

AUDIO_CACHE_DIR = "audio_cache"
os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

# Voices for Host A and Host B
VOICE_A = "en-US-GuyNeural"
VOICE_B = "en-US-JennyNeural"

def _get_doc_hash(doc_name: str) -> str:
    return hashlib.sha256(doc_name.encode('utf-8')).hexdigest()[:12]

def generate_podcast_script(doc_name: str) -> str:
    """Generates a two-speaker podcast script for a given document."""
    chunks = get_source_chunks(doc_name)
    if not chunks:
        raise ValueError("Document has no content.")
    
    # Concatenate chunks (limit to ~4000 words to avoid hitting context limits)
    full_text = " ".join([c.get("text", "") for c in chunks])
    full_text = " ".join(full_text.split()[:4000])
    
    prompt = f"""You are a professional podcast producer.
Write a 2-speaker educational podcast script summarizing the following document.
The speakers are HOST_A (a knowledgeable expert) and HOST_B (a curious co-host).
Keep it engaging, conversational, and around 5-8 minutes of spoken length.
Format the output EXACTLY like this:
HOST_A: Welcome to the podcast!
HOST_B: Thanks for having me...

Document text:
{full_text}
"""
    
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")
        
    # Since get_client isn't exported directly in llm.py, we instantiate Groq directly if needed,
    # but llm.py has `_groq_client` or we can just import Groq
    from groq import Groq
    from src.llm import PRIMARY_MODEL
    
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model=PRIMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2500
    )
    
    return getattr(response.choices[0].message, 'content', '').strip()

async def _synthesize_line(text: str, voice: str, output_file: str):
    """Synthesizes a single line of text into an MP3 file."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

async def synthesize_audio(script: str, output_path: str):
    """Parses script and synthesizes two-speaker audio into a single MP3."""
    lines = script.split('\n')
    
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    
    try:
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            
            voice = VOICE_A
            text = line
            if line.startswith("HOST_A:"):
                voice = VOICE_A
                text = line.replace("HOST_A:", "").strip()
            elif line.startswith("HOST_B:"):
                voice = VOICE_B
                text = line.replace("HOST_B:", "").strip()
                
            # Skip lines that are just formatting or actions like [laughs]
            if not text or text.startswith("[") or text.startswith("("):
                # Clean up action brackets inside text
                text = re.sub(r'\[.*?\]|\(.*?\)', '', text).strip()
                if not text:
                    continue
            
            temp_file = os.path.join(temp_dir, f"{i:04d}.mp3")
            await _synthesize_line(text, voice, temp_file)
            temp_files.append(temp_file)
            
        # Concatenate MP3s by appending bytes (valid for simple MP3 playback)
        with open(output_path, 'wb') as outfile:
            for f in temp_files:
                if os.path.exists(f):
                    with open(f, 'rb') as infile:
                        outfile.write(infile.read())
    finally:
        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)
        os.rmdir(temp_dir)

def get_or_generate_overview(doc_name: str) -> dict:
    """Entry point for the feature. Generates audio if not cached."""
    doc_hash = _get_doc_hash(doc_name)
    output_filename = f"{doc_hash}.mp3"
    output_path = os.path.join(AUDIO_CACHE_DIR, output_filename)
    
    if os.path.exists(output_path):
        return {
            "status": "ready", 
            "audio_url": f"/audio/overview/{output_filename}",
            "cached": True
        }
        
    # Not cached, generate it
    script = generate_podcast_script(doc_name)
    
    # Run async synthesis
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(synthesize_audio(script, output_path))
    finally:
        loop.close()
        
    return {
        "status": "ready",
        "audio_url": f"/audio/overview/{output_filename}",
        "cached": False
    }

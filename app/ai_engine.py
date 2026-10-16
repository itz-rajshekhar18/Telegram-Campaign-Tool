import httpx
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL

async def generate_reply(info_bank: str, conversation_history: str, user_message: str, language: str = "auto") -> str:
    """Generate campaign-aware reply using local Ollama."""
    system = f"""You are a helpful sales assistant for a campaign.
Use ONLY the following Information Bank to answer. Be natural, concise, and persuasive.
Detect and match the user's language (Hindi, English, or Hinglish).
If language is unclear, ask politely which they prefer.

Information Bank:
{info_bank}

Conversation so far:
{conversation_history}
"""
    prompt = f"User message: {user_message}\n\nReply:"
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 300}
                }
            )
            if resp.status_code == 200:
                return resp.json().get("response", "Sorry, I couldn't generate a reply right now.").strip()
    except Exception as e:
        return f"[AI offline] Please check Ollama. Error: {str(e)[:100]}"
    return "Thanks for your message. I'll get back to you shortly."

async def score_lead(messages: str) -> str:
    """Simple lead scoring via LLM."""
    prompt = f"""Based on this conversation, score the lead as hot, warm, or cold.
Reply with ONLY one word: hot, warm, or cold.

Conversation:
{messages[-1500:]}
"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
            )
            if resp.status_code == 200:
                result = resp.json().get("response", "cold").lower().strip()
                if "hot" in result:
                    return "hot"
                if "warm" in result:
                    return "warm"
    except:
        pass
    return "cold"

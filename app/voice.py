"""
Voice note handling (optional).
Requires:
- faster-whisper installed
- Piper TTS binary + voice models (Hindi: hi_IN-rohan-medium, English: en_US-lessac-medium)

Usage example (to be called from telegram_manager when a voice message arrives):
"""

import os
from pathlib import Path

VOICE_ENABLED = os.getenv("VOICE_ENABLED", "false").lower() == "true"

async def transcribe_voice(audio_path: str) -> str:
    if not VOICE_ENABLED:
        return "[Voice transcription disabled]"
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path)
        return " ".join([s.text for s in segments]).strip()
    except Exception as e:
        return f"[STT error: {e}]"

async def text_to_speech(text: str, language: str = "en", output_path: str = "reply.wav") -> str:
    if not VOICE_ENABLED:
        return None
    # Piper example (adjust path and voice model):
    # piper --model voices/hi_IN-rohan-medium.onnx --output_file reply.wav <<< "text"
    # For now this is a placeholder. Install Piper and wire the command.
    return None

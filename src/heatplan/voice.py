"""Hindi / English voice notes with Amazon Polly."""
from __future__ import annotations

import os

VOICES = {
    # (voice, engine, language) tried in order; regional availability varies.
    "hi": [("Kajal", "neural", "hi-IN"), ("Aditi", "standard", "hi-IN")],
    "en": [("Kajal", "neural", "en-IN"), ("Raveena", "standard", "en-IN")],
}


def synthesize(text: str, lang: str) -> bytes | None:
    if os.environ.get("VOICE_ENABLED", "true") != "true":
        return None
    try:
        import boto3
        polly = boto3.client("polly")
    except Exception as e:  # boto3 missing locally
        print(f"polly unavailable: {e}")
        return None
    for voice, engine, code in VOICES.get(lang, VOICES["en"]):
        try:
            res = polly.synthesize_speech(Text=text[:2900], VoiceId=voice, Engine=engine,
                                          LanguageCode=code, OutputFormat="mp3")
            return res["AudioStream"].read()
        except Exception as e:
            print(f"polly {voice}/{engine} failed: {e}")
    return None

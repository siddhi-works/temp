"""Check each configured API key with the cheapest possible real call.

    python -m scripts.check_providers

Costs: one Sarvam TTS call of ~20 characters (about ₹0.06, counted against the
budget cap). Gemini: one tiny prompt (free tier). WhatsApp: reads the phone
number's details; sends no message.
"""

import asyncio

import httpx

from app.config import get_settings
from app.db import init_models


async def main() -> None:
    s = get_settings()
    await init_models()

    if s.sarvam_api_key:
        from app.providers.tts import sarvam_tts

        try:
            mp3 = await sarvam_tts("नमस्कार, सूचना सेतू.", "mr")
            print(f"Sarvam TTS: OK ({len(mp3)} bytes of audio)")
        except Exception as exc:  # noqa: BLE001
            print(f"Sarvam TTS: FAILED - {exc}")
    else:
        print("Sarvam: no key")

    if s.gemini_api_key:
        from google import genai

        try:
            r = await genai.Client(api_key=s.gemini_api_key).aio.models.generate_content(model=s.gemini_model, contents="Reply with the word OK.")
            print(f"Gemini ({s.gemini_model}): OK -> {r.text.strip()[:20]}")
        except Exception as exc:  # noqa: BLE001
            print(f"Gemini: FAILED - {exc}")
    else:
        print("Gemini: no key")

    if s.whatsapp_token and s.whatsapp_phone_number_id:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                f"https://graph.facebook.com/{s.whatsapp_graph_version}/{s.whatsapp_phone_number_id}",
                headers={"Authorization": f"Bearer {s.whatsapp_token}"},
            )
            print(f"WhatsApp: {'OK' if r.status_code == 200 else 'FAILED'} -> {r.text[:200]}")
    else:
        print("WhatsApp: no token")


if __name__ == "__main__":
    asyncio.run(main())

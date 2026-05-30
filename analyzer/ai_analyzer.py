"""Calls Groq API to score a listing 1-10 and return an enriched dict with summary, green_flags, and red_flags."""

import json
import logging
from typing import Any

from groq import AsyncGroq

import config

logger = logging.getLogger(__name__)

_CLIENT = AsyncGroq(api_key=config.GROQ_API_KEY)

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = (
    "You are a German apartment hunting assistant. "
    "Analyze listings for a young professional or student in Regensburg, Germany. "
    "Always respond with valid JSON only, no markdown, no explanation."
)

_USER_PROMPT_TEMPLATE = """\
Analyze this apartment listing and return a JSON object with exactly these keys:
- score: integer 1-10 (10 = perfect, 1 = avoid)
- green_flags: list of short strings (positive aspects)
- red_flags: list of short strings (warning signs)
- summary: string, exactly 2 sentences in English

Green flags to look for (use your own wording): private landlord (no agency), \
price below Regensburg market rate (~12-15 €/m²), near university or city center, \
furnished, pets allowed, balcony or garden.

Red flags to look for (use your own wording): agency fee (Provision or Makler), \
very high deposit (more than 3 months cold rent), suspiciously low price (possible scam), \
very short rental period, no description at all, urgent or pressure language.

Listing details:
Title: {title}
Price (cold rent): {price} €
Size: {size} m²
Rooms: {rooms}
District: {district}
Available from: {available_from}
Description: {description}

Respond with valid JSON only. Example shape:
{{"score": 7, "green_flags": ["private landlord", "balcony"], "red_flags": ["high deposit"], "summary": "Sentence one. Sentence two."}}"""

_FALLBACK: dict[str, Any] = {
    "score": 5,
    "green_flags": [],
    "red_flags": [],
    "summary": "Analysis unavailable.",
}


def _build_user_prompt(listing: dict[str, Any]) -> str:
    return _USER_PROMPT_TEMPLATE.format(
        title=listing.get("title", "N/A"),
        price=listing.get("price", "N/A"),
        size=listing.get("size", "N/A"),
        rooms=listing.get("rooms", "N/A"),
        district=listing.get("district", "N/A"),
        available_from=listing.get("available_from", "N/A"),
        description=listing.get("description", ""),
    )


def _parse_response(content: str) -> dict[str, Any]:
    try:
        # Strip accidental markdown fences if the model disobeys
        text = content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        return {
            "score": int(data.get("score", 5)),
            "green_flags": [str(f) for f in data.get("green_flags", [])],
            "red_flags": [str(f) for f in data.get("red_flags", [])],
            "summary": str(data.get("summary", "Analysis unavailable.")),
        }
    except Exception as exc:
        logger.warning("Failed to parse Groq response: %s | raw: %.200s", exc, content)
        return _FALLBACK.copy()


async def analyze_listing(listing: dict[str, Any]) -> dict[str, Any]:
    """Return the listing dict enriched with score, green_flags, red_flags, summary."""
    try:
        response = await _CLIENT.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(listing)},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        content = response.choices[0].message.content or ""
        analysis = _parse_response(content)
    except Exception as exc:
        logger.error("Groq API error for listing %s: %s", listing.get("url", "?"), exc)
        analysis = _FALLBACK.copy()

    return {**listing, **analysis}

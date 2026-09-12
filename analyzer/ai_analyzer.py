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
    "You are a Berlin apartment hunting assistant. "
    "Analyze WG-Zimmer (room in shared flat) listings for a student doing a 6-month internship "
    "at Tesla in Grünheide, living in east Berlin. "
    "The internship runs from 1 October 2026 to end of March 2027 — the room must be available by 1 Oct 2026. "
    "Always respond with valid JSON only, no markdown, no explanation."
)

_USER_PROMPT_TEMPLATE = """\
Analyze this WG-Zimmer listing and return a JSON object with exactly these keys:
- score: integer 1-10 (10 = perfect, 1 = avoid)
- green_flags: list of short strings (positive aspects)
- red_flags: list of short strings (warning signs)
- summary: string, exactly 2 sentences in English
- transit_ostbahnhof: string — estimated public transit time from this district to Berlin Ostbahnhof (e.g. "~12 min S-Bahn")
- transit_ostkreuz: string — estimated public transit time from this district to Berlin Ostkreuz (e.g. "~8 min S-Bahn")

Green flags to look for: private landlord (no agency), price below Berlin market rate, \
furnished room, short-term or 6-month rental ok, no Anmeldung required, near S-Bahn, \
bills included (Nebenkosten), balcony or garden, quiet flatmates.

Red flags to look for: agency fee (Provision or Makler), very high deposit (more than 3 months), \
suspiciously low price (possible scam), very short rental period (less than 3 months), \
no description at all, urgent or pressure language, Anmeldung required.

For transit times, use your knowledge of Berlin S-Bahn/U-Bahn. \
Ostbahnhof and Ostkreuz are both in east Berlin — estimate realistically based on district.

Listing details:
Title: {title}
Price (cold rent): {price} €
Size: {size} m²
Rooms: {rooms}
District: {district}
Available from: {available_from}
Description: {description}

Respond with valid JSON only. Example shape:
{{"score": 7, "green_flags": ["private landlord", "furnished"], "red_flags": ["high deposit"], \
"summary": "Sentence one. Sentence two.", \
"transit_ostbahnhof": "~10 min S-Bahn", "transit_ostkreuz": "~7 min S-Bahn"}}"""

_FALLBACK: dict[str, Any] = {
    "score": 5,
    "green_flags": [],
    "red_flags": [],
    "summary": "Analysis unavailable.",
    "transit_ostbahnhof": "N/A",
    "transit_ostkreuz": "N/A",
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
            "transit_ostbahnhof": str(data.get("transit_ostbahnhof", "N/A")),
            "transit_ostkreuz": str(data.get("transit_ostkreuz", "N/A")),
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

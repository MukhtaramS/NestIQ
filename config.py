"""Loads .env and exposes typed constants for use across the NestIQ pipeline."""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(
            f"[NestIQ] Missing required environment variable: {key}\n"
            f"         Copy .env.example to .env and fill in all values."
        )
    return val


GROQ_API_KEY: str           = _require("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN: str     = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str       = _require("TELEGRAM_CHAT_ID")
KLEINANZEIGEN_EMAIL: str    = _require("KLEINANZEIGEN_EMAIL")
KLEINANZEIGEN_PASSWORD: str = _require("KLEINANZEIGEN_PASSWORD")
APPLICANT_NAME: str         = _require("APPLICANT_NAME")
APPLICANT_INTRO: str        = _require("APPLICANT_INTRO")
OPENROUTER_API_KEY: str     = _require("OPENROUTER_API_KEY")

CITY: str     = os.getenv("CITY", "Regensburg")
MAX_RENT: int = int(os.getenv("MAX_RENT", "1000"))
MIN_ROOMS: int = int(os.getenv("MIN_ROOMS", "1"))

# Optional applicant contact-form fields for Kleinanzeigen
APPLICANT_ZIP: str    = os.getenv("APPLICANT_ZIP", "")
APPLICANT_SCHUFA: str = os.getenv("APPLICANT_SCHUFA", "Keine Angabe")

KLEINANZEIGEN_URL = "https://www.kleinanzeigen.de/s-wohnung-mieten/regensburg/k0c203l7636"
WGGESUCHT_URL     = "https://www.wg-gesucht.de/wg-zimmer-in-Regensburg.111.0.1.0.html"

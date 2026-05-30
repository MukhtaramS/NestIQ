# NestIQ

AI-powered apartment finding agent for Germany.

NestIQ scrapes listings from Kleinanzeigen, filters and scores them with AI (Groq), sends the best ones to your Telegram, and auto-applies via an OpenRouter-driven Playwright agent when you tap YES.

## Setup

1. Clone the repo and install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

2. Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

3. Run the agent:

```bash
python main.py         # continuous loop every 10 minutes
python main.py --once  # single run for testing
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | ✅ | Groq API key for AI listing scoring |
| `OPENROUTER_API_KEY` | ✅ | OpenRouter key for the browser agent (Gemini Flash) |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram bot token |
| `TELEGRAM_CHAT_ID` | ✅ | Your personal Telegram chat ID |
| `KLEINANZEIGEN_EMAIL` | ✅ | Kleinanzeigen account email |
| `KLEINANZEIGEN_PASSWORD` | ✅ | Kleinanzeigen account password |
| `APPLICANT_NAME` | ✅ | Your full name (e.g. `Max Mustermann`) |
| `APPLICANT_INTRO` | ✅ | One-sentence intro used in the contact message |
| `APPLICANT_ZIP` | ✅ | Your current postcode (required by Kleinanzeigen's form) |
| `APPLICANT_SCHUFA` | ❌ | Schufa status shown in the form (default: `Keine Angabe`) |
| `CITY` | ❌ | Target city (default: `Regensburg`) |
| `MAX_RENT` | ❌ | Maximum cold rent in EUR (default: `1000`) |
| `MIN_ROOMS` | ❌ | Minimum rooms (default: `1`) |

## Pipeline

```
Scraper → Dedup (SQLite) → Filter (config) → AI Score (Groq) → Telegram card → Browser agent (on YES)
```

## Architecture

See [CLAUDE.md](CLAUDE.md) for full developer documentation.

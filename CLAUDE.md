# NestIQ — Claude Code Guide

## What this project is
NestIQ is an AI-powered apartment finding agent for Germany.
It scrapes listings, filters them, scores them with AI, sends the best ones to Telegram, and uses OpenClaw to auto-apply when the user approves.

## Architecture — pipeline order
1. scraper/kleinanzeigen.py → fetches raw listings from Kleinanzeigen
2. db/storage.py → deduplicates (skip already-seen URLs)
3. config.py → hard filter rules (MAX_RENT, MIN_ROOMS)
4. analyzer/ai_analyzer.py → scores listing 1-10 via Groq API
5. bot/telegram_bot.py → sends card to Telegram with YES/NO buttons
6. mailer/openclaw.py → fires when user taps YES, triggers OpenClaw agent
7. main.py → orchestrates all of the above in a loop every 10 minutes

## Key conventions
- All async/await — do not use synchronous requests anywhere
- All config comes from config.py — never hardcode API keys or URLs
- Every listing is a plain dict with keys: url, title, price, size, rooms, district, available_from, first_photo_url, description, score, green_flags, red_flags, summary
- Errors are always caught and logged — never let one bad listing crash the pipeline
- DB is SQLite via db/storage.py — never import sqlite3 directly outside that file

## Environment variables (all in .env)
- GROQ_API_KEY — Groq API for AI scoring
- TELEGRAM_BOT_TOKEN — Telegram bot token
- TELEGRAM_CHAT_ID — your personal chat ID to receive listings
- OPENCLAW_API_KEY — OpenClaw agent API key
- CITY — target city (default: Regensburg)
- MAX_RENT — maximum cold rent in EUR (default: 1000)
- MIN_ROOMS — minimum number of rooms (default: 1)

## Platform scraping targets
- Kleinanzeigen Regensburg: https://www.kleinanzeigen.de/s-wohnung-mieten/regensburg/k0c203l7636
- WG-Gesucht Regensburg: https://www.wg-gesucht.de/wg-zimmer-in-Regensburg.111.0.1.0.html

## Module responsibilities — one-liner each
- config.py: loads .env and exposes typed constants
- db/storage.py: all SQLite reads and writes, nothing else
- scraper/base.py: abstract BaseScraper class
- scraper/kleinanzeigen.py: Kleinanzeigen-specific scraper
- analyzer/ai_analyzer.py: Groq API call, returns enriched listing dict
- bot/telegram_bot.py: sends Telegram cards, handles YES/NO callbacks
- mailer/openclaw.py: triggers OpenClaw agent on YES approval
- main.py: pipeline loop, wires everything together

## Running the project
- Full loop: python main.py
- Single run (testing): python main.py --once
- Test scraper only: python test_scraper.py
- Test analyzer only: python test_analyzer.py

## What NOT to do
- Do not add a web UI or REST API — Telegram is the only interface
- Do not use synchronous httpx — always use async client
- Do not import from main.py in any submodule
- Do not store API keys anywhere except .env

"""Pipeline loop — wires scraper, dedup, filter, AI scorer, Telegram bot, and OpenClaw together."""

import argparse
import asyncio
import logging

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler

import config
import db.storage as storage
from analyzer.ai_analyzer import analyze_listing
from bot.telegram_bot import handle_callback, handle_start, handle_status, send_listing
from scraper.kleinanzeigen import KleinanzeigenScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_POLL_INTERVAL = 600  # 10 minutes


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def run_pipeline() -> None:
    """Single end-to-end pass: scrape → dedup → filter → score → notify."""
    logger.info("Pipeline started")
    scraper = KleinanzeigenScraper()

    try:
        raw_listings = await scraper.fetch_listings()
    except Exception as exc:
        logger.error("Scraper failed: %s", exc)
        return

    logger.info("Fetched %d raw listings", len(raw_listings))
    new_count = 0

    for listing in raw_listings:
        url = listing.get("url", "")
        if not url:
            continue

        # ── 1. Deduplicate ────────────────────────────────────────────────────
        if storage.is_seen(url):
            logger.debug("Already seen, skipping: %s", url)
            continue

        # ── 2. Hard filter ────────────────────────────────────────────────────
        price = listing.get("price") or 0
        rooms = listing.get("rooms") or 0

        if price > config.MAX_RENT:
            logger.debug("Over budget (€%s): %s", price, url)
            continue

        if rooms < config.MIN_ROOMS:
            logger.debug("Too few rooms (%s): %s", rooms, url)
            continue

        # ── 3. AI scoring ─────────────────────────────────────────────────────
        try:
            enriched = await analyze_listing(listing)
        except Exception as exc:
            logger.error("Analyzer failed for %s: %s", url, exc)
            enriched = listing  # send un-scored rather than drop it

        # ── 4. Persist ────────────────────────────────────────────────────────
        try:
            storage.save_listing(enriched)
        except Exception as exc:
            logger.error("DB save failed for %s: %s", url, exc)
            continue

        # ── 5. Notify via Telegram ────────────────────────────────────────────
        try:
            await send_listing(enriched)
            new_count += 1
        except Exception as exc:
            logger.error("Telegram send failed for %s: %s", url, exc)

    logger.info("Pipeline done — %d new listings sent", new_count)


# ── Job wrapper (called by python-telegram-bot job queue) ────────────────────

async def _pipeline_job(context) -> None:  # noqa: ANN001
    await run_pipeline()


# ── Entry points ──────────────────────────────────────────────────────────────

async def run_once() -> None:
    """Run the pipeline exactly once and exit (for testing)."""
    await run_pipeline()


def run_loop() -> None:
    """Start the Telegram bot + schedule the pipeline every 10 minutes."""
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Register command handlers
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("status", handle_status))

    # Register YES/NO callback handler
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Schedule the scraping pipeline as a repeating job
    application.job_queue.run_repeating(
        _pipeline_job,
        interval=_POLL_INTERVAL,
        first=0,          # run immediately on startup, then every 10 min
        name="pipeline",
    )

    logger.info(
        "NestIQ started — polling Telegram, pipeline runs every %ds. "
        "City=%s  MAX_RENT=%s  MIN_ROOMS=%s",
        _POLL_INTERVAL, config.CITY, config.MAX_RENT, config.MIN_ROOMS,
    )
    application.run_polling(drop_pending_updates=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NestIQ apartment agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the scrape-and-notify pipeline once, then exit (no Telegram polling)",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_once())
    else:
        run_loop()

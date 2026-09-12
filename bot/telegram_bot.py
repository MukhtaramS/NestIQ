"""Sends formatted listing cards to Telegram and handles YES/NO inline keyboard callbacks."""

import hashlib
import html
import logging
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, ContextTypes

import config
import db.storage as storage
import mailer.openclaw as openclaw

logger = logging.getLogger(__name__)

_bot = Bot(token=config.TELEGRAM_BOT_TOKEN)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _score_emoji(score: int) -> str:
    if score >= 7:
        return "🟢"
    if score >= 5:
        return "🟡"
    return "🔴"


def _verdict(score: int) -> str:
    if score >= 8:
        return "Excellent match"
    if score >= 7:
        return "Good match"
    if score >= 5:
        return "Worth a look"
    if score >= 3:
        return "Below average"
    return "Avoid"


def _landlord_line(listing: dict[str, Any]) -> str:
    flags_text = " ".join(listing.get("green_flags", [])).lower()
    desc_text = (listing.get("description", "") or "").lower()
    red_text = " ".join(listing.get("red_flags", [])).lower()

    if "privat" in flags_text or "private landlord" in flags_text:
        return "🏠 Private landlord"
    if "makler" in red_text or "provision" in red_text or "agency" in red_text:
        return "🏢 Agency listing (Makler)"
    if "makler" in desc_text or "provision" in desc_text:
        return "🏢 Agency listing (Makler)"
    return ""


def _format_message(listing: dict[str, Any]) -> str:
    score = int(listing.get("score") or 5)
    emoji = _score_emoji(score)
    verdict = _verdict(score)

    # Escape all user-supplied strings — scraped data may contain <, >, & which
    # would break Telegram's HTML parser and cause BadRequest: Can't parse entities.
    title        = html.escape(listing.get("title", "N/A") or "N/A")
    price        = listing.get("price", "?")   # numeric, no escaping needed
    size         = listing.get("size", "?")    # numeric
    rooms        = listing.get("rooms", "?")   # numeric
    district     = html.escape(listing.get("district", "?") or "?")
    available_from = html.escape(listing.get("available_from", "?") or "?")
    description  = html.escape((listing.get("description", "") or "")[:150])
    url          = html.escape(listing.get("url", "") or "")
    transit_ostbahnhof = html.escape(listing.get("transit_ostbahnhof", "N/A") or "N/A")
    transit_ostkreuz   = html.escape(listing.get("transit_ostkreuz",   "N/A") or "N/A")

    green_flags: list[str] = listing.get("green_flags", []) or []
    red_flags:   list[str] = listing.get("red_flags",   []) or []

    lines: list[str] = [
        f"{emoji} <b>Score: {score}/10</b> — {verdict}",
        "",
        f"<b>{title}</b>",
        f"💶 {price} € cold  ·  📐 {size} m²  ·  🛏 {rooms} rooms",
        f"📍 {district}  ·  📅 Available from: {available_from}",
        f"🚆 Ostbahnhof: {transit_ostbahnhof}  ·  Ostkreuz: {transit_ostkreuz}",
    ]

    landlord = _landlord_line(listing)
    if landlord:
        lines.append(landlord)

    lines.append("")

    for flag in green_flags:
        lines.append(f"✅ {html.escape(flag)}")

    for flag in red_flags:
        lines.append(f"⚠️ {html.escape(flag)}")

    if description:
        lines.append("")
        lines.append(f"<i>{description}…</i>")

    lines.append(f'<a href="{url}">🔗 View listing</a>')

    return "\n".join(lines)


def _keyboard(url: str) -> InlineKeyboardMarkup:
    h = _url_hash(url)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes — Apply", callback_data=f"yes_{h}"),
        InlineKeyboardButton("❌ No — Skip",   callback_data=f"no_{h}"),
    ]])


# ── Command handlers ─────────────────────────────────────────────────────────

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — welcome message."""
    await update.message.reply_text(
        "👋 <b>Welcome to NestIQ!</b>\n"
        "\n"
        "I'm your AI-powered apartment hunting agent for Regensburg.\n"
        "\n"
        "I scrape fresh listings every 10 minutes, score them with AI, "
        "and send the best ones here. Tap <b>✅ Yes</b> on any card to "
        "auto-apply, or <b>❌ No</b> to skip it.\n"
        "\n"
        "Use /status to see your current search settings.",
        parse_mode=ParseMode.HTML,
    )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — show active search config."""
    await update.message.reply_text(
        "⚙️ <b>NestIQ — current settings</b>\n"
        "\n"
        f"📍 City: <b>{html.escape(config.CITY)}</b>\n"
        f"💶 Max rent: <b>{config.MAX_RENT} €</b> (cold)\n"
        f"🛏 Min rooms: <b>{config.MIN_ROOMS}</b>\n"
        "\n"
        "🔄 Pipeline runs every <b>10 minutes</b>.\n"
        "Change settings in your <code>.env</code> file and restart.",
        parse_mode=ParseMode.HTML,
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def send_listing(listing: dict[str, Any]) -> None:
    """Send a formatted listing card to TELEGRAM_CHAT_ID."""
    text = _format_message(listing)
    keyboard = _keyboard(listing.get("url", ""))
    photo_url = listing.get("first_photo_url", "")

    try:
        if photo_url:
            await _bot.send_photo(
                chat_id=config.TELEGRAM_CHAT_ID,
                photo=photo_url,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            await _bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
    except Exception as exc:
        logger.error("Failed to send Telegram message for %s: %s", listing.get("url"), exc)


async def handle_callback(update: Update, context: CallbackContext) -> None:
    """Handle YES/NO inline button presses."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data: str = query.data or ""
    pending = storage.get_pending_listings()

    # Resolve the listing from the hash embedded in callback_data
    action, _, h = data.partition("_")
    matched = next(
        (lst for lst in pending if _url_hash(lst["url"]) == h),
        None,
    )

    if not matched:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("⚠️ Listing not found or already decided.")
        return

    url = matched["url"]

    if action == "yes":
        try:
            await openclaw.trigger_application(matched)
            storage.mark_decision(url, "yes")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"✅ <b>Application triggered!</b>\nOpenClaw is processing: {url}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.error("OpenClaw trigger failed for %s: %s", url, exc)
            await query.message.reply_text(
                f"❌ Failed to trigger application: {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )

    elif action == "no":
        storage.mark_decision(url, "no")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("⏭ Skipped.")


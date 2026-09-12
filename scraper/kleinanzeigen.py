"""Kleinanzeigen-specific scraper — fetches and parses rental listings for the target city."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

import config
from scraper.base import BaseScraper

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_BASE_URL = "https://www.kleinanzeigen.de"


def _parse_price(text: str) -> int | None:
    """Extract integer EUR value from any text containing a price.

    Handles: '750 €', '1.200 €', '1,200 €', 'ab 850€'
    """
    m = re.search(r"(\d[\d.,]*)\s*€", text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def _parse_size(text: str) -> int | None:
    """Extract square metres from text like '51,38 m²' or '80 m²'."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", text, re.IGNORECASE)
    if m:
        return int(float(m.group(1).replace(",", ".")))
    return None


def _parse_rooms(text: str) -> float | None:
    """Extract room count from text like '2 Zi.' or '3 Zimmer' or '2,5 Zi.'."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*Zi(?:mmer|\.)?", text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


_GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8,
    "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}


def _parse_posted_date(card) -> datetime | None:
    """Return a timezone-aware datetime for when the card was posted, or None.

    Kleinanzeigen uses these formats in .aditem-main--top--right:
      - "Heute, 14:32"   → today at that time
      - "Gestern, 09:15" → yesterday at that time
      - "12.05.2025"     → explicit date (no time component)
      - "12. Mai 2025"   → long German format
    """
    date_el = card.find("div", class_="aditem-main--top--right")
    if not date_el:
        return None

    raw = date_el.get_text(strip=True)
    now = datetime.now(timezone.utc)

    try:
        lower = raw.lower()

        # "Heute, HH:MM"
        m = re.match(r"heute[,\s]+(\d{1,2}):(\d{2})", lower)
        if m:
            return now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                               second=0, microsecond=0)

        # "Gestern, HH:MM"
        m = re.match(r"gestern[,\s]+(\d{1,2}):(\d{2})", lower)
        if m:
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                     second=0, microsecond=0)

        # "DD.MM.YYYY"
        m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
        if m:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)

        # "DD. Monatsname YYYY"
        m = re.match(r"(\d{1,2})\.\s*([a-zäöü]+)\s+(\d{4})", lower)
        if m:
            month = _GERMAN_MONTHS.get(m.group(2))
            if month:
                return datetime(int(m.group(3)), month, int(m.group(1)),
                                tzinfo=timezone.utc)

    except (ValueError, AttributeError):
        pass

    return None


def _is_within_24h(card) -> bool:
    """Return True if posted within the last 24 hours, or if the date can't be parsed."""
    posted = _parse_posted_date(card)
    if posted is None:
        return True  # can't parse → include to be safe
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return posted >= cutoff


def _parse_available_from(description: str) -> str:
    """Extract 'frei ab ...' date from description text if present."""
    m = re.search(r"frei\s+ab\s+([\w.\s]+?)(?:[,.]|$)", description, re.IGNORECASE)
    return m.group(1).strip() if m else "k.A."


def _parse_card(card) -> dict | None:
    try:
        link = card.find("a", class_="ellipsis")
        if not link:
            return None

        url = _BASE_URL + link["href"]
        title = link.get_text(strip=True)

        # ── Price ─────────────────────────────────────────────────────────────
        # Exact class from live HTML; fall back to any-class substring match,
        # then to a full-card text scan so we never silently lose a price.
        price_el = (
            card.find("p", class_="aditem-main--middle--price-shipping--price")
            or card.find(class_=lambda c: c and any("price" in cls for cls in c))
        )
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text) or _parse_price(card.get_text(" ", strip=True))

        # ── Description ───────────────────────────────────────────────────────
        # Class changed from "…description--text" to "…middle--description".
        desc_el = (
            card.find("p", class_="aditem-main--middle--description")
            or card.find("p", class_=lambda c: c and any("description" in cls for cls in c))
        )
        description = desc_el.get_text(strip=True) if desc_el else ""

        # ── Size and rooms ────────────────────────────────────────────────────
        # Live HTML: <p class="aditem-main--middle--tags">51,38 m² · 2 Zi.</p>
        # No div.simpletag elements exist any more.
        tags_el = card.find("p", class_="aditem-main--middle--tags")
        tags_text = tags_el.get_text(" ", strip=True) if tags_el else ""
        full_text = card.get_text(" ", strip=True)  # last-resort fallback

        size  = _parse_size(tags_text)  or _parse_size(full_text)
        rooms = _parse_rooms(tags_text) or _parse_rooms(full_text)

        # ── District ──────────────────────────────────────────────────────────
        loc_el = card.find("div", class_="aditem-main--top--left")
        district = loc_el.get_text(strip=True) if loc_el else ""

        # ── Photo ─────────────────────────────────────────────────────────────
        img_el = card.find("div", class_="galleryimage-element")
        first_photo_url = (
            img_el["data-imgsrc"] if img_el and img_el.has_attr("data-imgsrc") else ""
        )

        if not url or not title:
            return None  # only drop cards that have no identity at all

        if price is None:
            logger.debug("Price not parsed for: %s", url)
        if size is None:
            logger.debug("Size not parsed for: %s", url)

        return {
            "url": url,
            "title": title,
            "price": price,
            "size": size,
            "rooms": rooms,
            "district": district,
            "available_from": _parse_available_from(description),
            "first_photo_url": first_photo_url,
            "description": description,
            "score": None,
            "green_flags": [],
            "red_flags": [],
            "summary": "",
        }
    except Exception as exc:
        logger.warning("Failed to parse card: %s", exc)
        return None


_MAX_PAGES = 20


def _page_url(page: int) -> str:
    """Build the URL for a given page number.

    Page 1 uses the base URL from config unchanged.
    Page 2+ inserts 'seite:{n}/' before the 'k0c…' segment.
    e.g. …/regensburg/seite:2/k0c203l7636
    """
    if page == 1:
        return config.KLEINANZEIGEN_URL
    # Insert seite:N/ immediately before the k0c… token
    return re.sub(r"(k0c[^/]*)", f"seite:{page}/\\1", config.KLEINANZEIGEN_URL)


class KleinanzeigenScraper(BaseScraper):
    async def fetch_listings(self) -> list[dict]:
        listings = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_HEADERS["User-Agent"],
                locale="de-DE",
                extra_http_headers={
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                },
            )
            page = await context.new_page()

            try:
                for pg in range(1, _MAX_PAGES + 1):
                    url = _page_url(pg)
                    await asyncio.sleep(random.uniform(2, 4))

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        # Wait for listings to render
                        try:
                            await page.wait_for_selector("article.aditem", timeout=10_000)
                        except PWTimeout:
                            pass  # no listings on this page — will check below
                    except Exception as exc:
                        logger.error("Failed to fetch Kleinanzeigen page %d: %s", pg, exc)
                        break

                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.find_all("article", class_="aditem")
                    logger.info("Page %d — found %d cards", pg, len(cards))

                    if not cards:
                        logger.info("Page %d returned 0 cards — stopping pagination", pg)
                        break

                    page_new = 0
                    for card in cards:
                        # Skip sponsored/premium insertions
                        if "aditem-premium" in card.get("class", []):
                            continue
                        # Skip listings older than 24 hours
                        if not _is_within_24h(card):
                            logger.debug("Skipping stale listing (>24h old)")
                            continue
                        listing = _parse_card(card)
                        if listing:
                            listings.append(listing)
                            page_new += 1

                    logger.info("Page %d — added %d new listings (total so far: %d)",
                                pg, page_new, len(listings))

            finally:
                await context.close()
                await browser.close()

        logger.info("Pagination complete — %d valid listings from Kleinanzeigen", len(listings))
        return listings

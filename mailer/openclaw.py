"""AI-driven Playwright agent that applies to Kleinanzeigen listings via OpenRouter."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import aiohttp
from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PWTimeout

import config

logger = logging.getLogger(__name__)

_SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "kleinanzeigen_session.json")
_LOGIN_URL    = "https://www.kleinanzeigen.de/m-einloggen.html"
_NAV_TIMEOUT  = 15_000   # ms
_ACT_TIMEOUT  = 8_000    # ms
_MAX_STEPS    = 12

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL          = "google/gemini-2.0-flash-lite-001"

# Split "Max Mustermann" → first="Max", last="Mustermann"
_name_parts  = config.APPLICANT_NAME.split(None, 1)
_FIRST_NAME  = _name_parts[0]
_LAST_NAME   = _name_parts[1] if len(_name_parts) > 1 else ""

_SYSTEM_PROMPT = """\
You are an apartment application agent controlling a real web browser.
You will receive a screenshot and a short HTML summary of the current page.

The static applicant fields (name, zip, Schufa, salutation) have already been
pre-filled for you. Your ONLY remaining tasks are:
  1. Fill the message textarea (#viewad-contact-message) with a polite German
     enquiry message (3-4 sentences, professional, sign off with first name).
  2. Click the submit button (button.viewad-contact-submit, text "Nachricht senden").

Applicant first name: {first_name}
Applicant intro:      {intro}

At every step respond with ONLY a valid JSON object — no markdown, no explanation:
{{
  "action":   "fill" | "click" | "scroll" | "done" | "fail",
  "selector": "<CSS selector — empty string when action is scroll>",
  "value":    "<text to type — only for fill, else empty>",
  "reason":   "<one sentence explaining this step>"
}}

Use "scroll" if the submit button or textarea is not yet visible.
Use "done" ONLY after the submit button has been clicked and the page shows a
confirmation (success banner, form disappears, or URL changes).
Do NOT return "done" while the form or submit button is still on the page.
Use "fail" if the listing is closed, blocked, or sending is truly impossible.\
"""


# ── Cookie dismissal ──────────────────────────────────────────────────────────

_COOKIE_SELECTORS = [
    "#gdpr-banner-accept",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "[data-testid='uc-accept-all-button']",
    ".sp_choice_type_11",
]


async def _dismiss_cookies(page: Page) -> None:
    for sel in _COOKIE_SELECTORS:
        try:
            await page.click(sel, timeout=2_500)
            await page.wait_for_timeout(700)
            logger.info("Cookie banner dismissed via: %s", sel)
            return
        except Exception:
            pass


# ── Session helpers ───────────────────────────────────────────────────────────

def _session_exists() -> bool:
    return os.path.isfile(_SESSION_FILE)


def _delete_session() -> None:
    if os.path.isfile(_SESSION_FILE):
        os.remove(_SESSION_FILE)
        logger.info("Session file deleted")


async def _save_session(context: BrowserContext) -> None:
    state = await context.storage_state()
    with open(_SESSION_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    logger.info("Session saved → %s", _SESSION_FILE)


async def _is_logged_in(page: Page) -> bool:
    """
    True when the listing page shows the authenticated contact button.

    Kleinanzeigen renders two different buttons:
      - NOT logged in → #viewad-contact-button-login  (redirects to login on click)
      - Logged in     → #viewad-contact-button        (opens the inline contact form)
    """
    url = page.url
    if "einloggen" in url or "login.kleinanzeigen" in url:
        return False
    try:
        btn = await page.query_selector("#viewad-contact-button")
        if btn:
            return True
        # Fallback: "Meine Anzeigen" nav link is only present when authenticated
        acct = await page.query_selector("a[href*='m-meine-anzeigen']")
        if acct:
            login_btn = await page.query_selector("#viewad-contact-button-login")
            return login_btn is None
    except Exception:
        pass
    return False


_DEBUG_LOGIN1 = os.path.join(os.path.dirname(__file__), "..", "debug_login.png")
_DEBUG_LOGIN2 = os.path.join(os.path.dirname(__file__), "..", "debug_login_step2.png")


async def _login(page: Page) -> None:
    """Two-step Kleinanzeigen login: email → Weiter → password → Einloggen."""
    logger.info("Logging in to Kleinanzeigen …")
    await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    await _dismiss_cookies(page)

    data = await page.screenshot(type="png", full_page=True)
    with open(_DEBUG_LOGIN1, "wb") as fh:
        fh.write(data)

    await page.wait_for_selector("input[type='email']", state="visible", timeout=15_000)
    await page.fill("input[type='email']", config.KLEINANZEIGEN_EMAIL)
    await page.wait_for_timeout(500)
    await page.get_by_role("button", name="Weiter").click()

    await page.wait_for_timeout(3_000)
    data = await page.screenshot(type="png", full_page=True)
    with open(_DEBUG_LOGIN2, "wb") as fh:
        fh.write(data)

    await page.wait_for_selector("input[type='password']", state="visible", timeout=10_000)
    await page.fill("input[type='password']", config.KLEINANZEIGEN_PASSWORD)
    await page.wait_for_timeout(500)

    try:
        await page.get_by_text("Einloggen", exact=True).click()
    except Exception:
        await page.locator("button[type='submit']").click()

    await page.wait_for_timeout(3_000)

    try:
        await page.wait_for_url(
            lambda u: "einloggen" not in u and "login.kleinanzeigen" not in u,
            timeout=8_000,
        )
    except PWTimeout:
        raise RuntimeError(
            "Login did not redirect after password step — "
            "check credentials and debug_login_step2.png"
        )

    logger.info("Login successful → %s", page.url)
    await _save_session(page.context)


# ── Contact form opener ───────────────────────────────────────────────────────

async def _open_contact_form(page: Page) -> None:
    """
    Click the authenticated contact button and wait for the inline form to expand.

    When logged in, the button is #viewad-contact-button (no -login suffix).
    The form expands inline below the button — there is NO MFP modal involved.
    """
    try:
        await page.evaluate("document.querySelector('#viewad-contact-button').scrollIntoView()")
        await page.wait_for_timeout(300)
    except Exception:
        pass

    await page.click("#viewad-contact-button", timeout=_ACT_TIMEOUT)
    logger.info("Clicked #viewad-contact-button — waiting for form to expand")

    await page.wait_for_selector(
        "#viewad-contact-message",
        state="visible",
        timeout=_ACT_TIMEOUT,
    )
    logger.info("Contact form open — #viewad-contact-message is visible")


# ── Static applicant field prefill ───────────────────────────────────────────

async def _prefill_applicant_data(page: Page) -> None:
    """
    Deterministically fill all static applicant fields before handing off to the AI.
    The AI then only needs to write the message text and click submit.
    """
    async def _try_fill(selector: str, value: str) -> None:
        if not value:
            return
        try:
            await page.fill(selector, value, timeout=3_000)
        except Exception:
            pass

    async def _try_select(selector: str, label: str) -> None:
        if not label:
            return
        try:
            await page.select_option(selector, label=label, timeout=3_000)
        except Exception:
            try:
                await page.select_option(selector, value=label, timeout=3_000)
            except Exception:
                pass

    # Scroll the form into view before filling
    try:
        await page.evaluate(
            "document.querySelector('#contact-contactFirstName-input')?.scrollIntoView()"
        )
        await page.wait_for_timeout(300)
    except Exception:
        pass

    await _try_select("#salutation", "Herr")
    await _try_fill("#contact-contactFirstName-input", _FIRST_NAME)
    await _try_fill("#contact-contactLastName-input", _LAST_NAME)
    await _try_fill("#zipCode", config.APPLICANT_ZIP)
    await _try_select("#currentSchufaInformation", config.APPLICANT_SCHUFA)

    logger.info(
        "Pre-filled applicant data: name=%s %s  zip=%s  schufa=%s",
        _FIRST_NAME, _LAST_NAME, config.APPLICANT_ZIP, config.APPLICANT_SCHUFA,
    )


# ── OpenRouter vision call ────────────────────────────────────────────────────

async def _ask_agent(
    screenshot_bytes: bytes, html_snippet: str, history: list[dict]
) -> dict:
    b64 = base64.b64encode(screenshot_bytes).decode()

    user_content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {
            "type": "text",
            "text": (
                f"Current page HTML (trimmed to 3000 chars):\n"
                f"{html_snippet[:3000]}\n\n"
                "What is the next action to fill and submit the rental application form?"
            ),
        },
    ]

    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT.format(
                first_name=_FIRST_NAME,
                last_name=_LAST_NAME,
                intro=config.APPLICANT_INTRO,
            ),
        },
        *history,
        {"role": "user", "content": user_content},
    ]

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/nestiq",
        "X-Title":       "NestIQ",
    }
    payload = {
        "model":       _MODEL,
        "messages":    messages,
        "max_tokens":  256,
        "temperature": 0.1,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            _OPENROUTER_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"OpenRouter {resp.status}: {body[:300]}")
            data = await resp.json()

    raw = data["choices"][0]["message"]["content"].strip()
    raw = re.sub(r'```json\s*', '', raw)
    raw = re.sub(r'```\s*', '', raw)
    start, end = raw.find('{'), raw.rfind('}')
    if start != -1 and end != -1:
        raw = raw[start:end + 1]

    try:
        action = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Agent returned invalid JSON: {exc}\nRaw: {raw[:200]}") from exc

    for key in ("action", "selector", "reason"):
        if key not in action:
            raise RuntimeError(f"Agent response missing key '{key}': {action}")
    # "value" is optional — default to empty string when absent
    action.setdefault("value", "")

    return action


# ── Action executor ───────────────────────────────────────────────────────────

async def _execute(page: Page, action: str, selector: str, value: str) -> None:
    if action == "click":
        try:
            await page.click(selector, timeout=_ACT_TIMEOUT)
        except Exception:
            await page.get_by_text(selector).first.click(timeout=_ACT_TIMEOUT)

    elif action == "fill":
        # Model sometimes returns empty selector for the message textarea — default it
        if not selector:
            selector = "#viewad-contact-message"
        await page.fill(selector, value, timeout=_ACT_TIMEOUT)

    elif action == "select":
        # Try by visible label first, then by value
        try:
            await page.select_option(selector, label=value, timeout=_ACT_TIMEOUT)
        except Exception:
            await page.select_option(selector, value=value, timeout=_ACT_TIMEOUT)

    elif action == "scroll":
        await page.evaluate("window.scrollBy(0, 400)")

    else:
        raise ValueError(f"Unknown action: {action!r}")


# ── Public entry point ────────────────────────────────────────────────────────

async def trigger_application(listing: dict[str, Any]) -> None:
    """Drive Playwright + OpenRouter AI to send a rental application message.

    Raises:
        ValueError:   if listing has no URL.
        RuntimeError: if the agent reports failure, exceeds max steps, or
                      OpenRouter returns an error.
    """
    url = listing.get("url", "")
    if not url:
        raise ValueError("Listing has no URL — cannot apply")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-features=Translate", "--lang=de-DE"],
        )

        if _session_exists():
            logger.info("Loading saved session")
            context = await browser.new_context(
                storage_state=_SESSION_FILE,
                locale="de-DE",
            )
        else:
            context = await browser.new_context(locale="de-DE")

        page = await context.new_page()
        await page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9"})

        try:
            if not _session_exists():
                await _login(page)

            logger.info("Navigating to listing: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            await _dismiss_cookies(page)
            await page.wait_for_timeout(1_500)

            # Verify the session is still valid by checking which contact button is shown
            if not await _is_logged_in(page):
                logger.warning("Session expired or invalid — performing fresh login")
                _delete_session()
                await context.close()
                context = await browser.new_context(locale="de-DE")
                page    = await context.new_page()
                await page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9"})
                await _login(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                await _dismiss_cookies(page)
                await page.wait_for_timeout(1_500)

            # Deterministically open the inline contact form and prefill static fields.
            # The AI loop then only needs to write the message and click submit.
            await _open_contact_form(page)
            await _prefill_applicant_data(page)

            history: list[dict] = []

            for step in range(1, _MAX_STEPS + 1):
                screenshot = await page.screenshot(type="png", full_page=False)
                html       = await page.content()

                logger.info("Step %d/%d — asking agent", step, _MAX_STEPS)
                action_obj = await _ask_agent(screenshot, html, history)

                act      = action_obj["action"]
                selector = action_obj["selector"]
                value    = action_obj["value"]
                reason   = action_obj["reason"]

                logger.info("Agent → action=%r  reason=%s", act, reason)
                history.append({"role": "assistant", "content": json.dumps(action_obj)})

                if act == "done":
                    # Guard: the form may not have been submitted yet — scroll to the
                    # submit button and click it regardless, then wait for confirmation.
                    try:
                        await page.evaluate(
                            "document.querySelector('button.viewad-contact-submit')"
                            "?.scrollIntoView({block:'center'})"
                        )
                        await page.wait_for_timeout(400)
                        submit_btn = page.locator("button.viewad-contact-submit")
                        if await submit_btn.count() > 0:
                            logger.info("Clicking submit button (submit guard)")
                            await submit_btn.first.click(timeout=_ACT_TIMEOUT)
                            await page.wait_for_timeout(2_000)
                    except Exception as exc:
                        logger.debug("Submit guard: %s", exc)
                    logger.info(
                        "Agent reports success after %d step(s) for: %s", step, url
                    )
                    await _save_session(context)
                    return

                if act == "fail":
                    raise RuntimeError(
                        f"Agent cannot send application for {url}: {reason}"
                    )

                try:
                    await _execute(page, act, selector, value)
                    history.append({
                        "role":    "user",
                        "content": f"Action executed: {act} on '{selector}'.",
                    })
                except Exception as exc:
                    err = f"Action failed ({act} on '{selector}'): {exc}"
                    logger.warning(err)
                    history.append({
                        "role":    "user",
                        "content": err + " Try a different selector or scroll.",
                    })

            raise RuntimeError(
                f"Agent did not complete after {_MAX_STEPS} steps for: {url}"
            )

        finally:
            await context.close()
            await browser.close()

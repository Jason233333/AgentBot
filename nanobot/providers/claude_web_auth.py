"""Claude Web authentication flow.

Connects to a Chrome instance via CDP, guides user through claude.ai login,
captures the sessionKey cookie, and persists credentials.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger


_CREDENTIALS_DIR = Path.home() / ".nanobot" / "credentials"
_CREDENTIALS_FILE = _CREDENTIALS_DIR / "claude-web.json"
_LOGIN_URL = "https://claude.ai/login"
_LOGIN_TIMEOUT_S = 300  # 5 minutes


def load_credentials() -> dict[str, Any]:
    """Load saved Claude Web credentials from disk."""
    if _CREDENTIALS_FILE.exists():
        try:
            return json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load Claude Web credentials from {}", _CREDENTIALS_FILE)
    return {}


def save_credentials(creds: dict[str, Any]) -> None:
    """Persist Claude Web credentials to disk."""
    _CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    _CREDENTIALS_FILE.write_text(
        json.dumps(creds, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Credentials saved to {}", _CREDENTIALS_FILE)


async def login_interactive(
    chrome_cdp_url: str = "http://127.0.0.1:9222",
    timeout_s: int = _LOGIN_TIMEOUT_S,
) -> dict[str, Any]:
    """Run interactive login flow via Chrome CDP.

    1. Connect to Chrome at chrome_cdp_url
    2. Navigate to claude.ai login page
    3. Wait for user to complete login (monitor cookies)
    4. Capture sessionKey cookie
    5. Save and return credentials

    Args:
        chrome_cdp_url: Chrome DevTools Protocol URL.
        timeout_s: Max seconds to wait for login.

    Returns:
        Dict with session_key, cookie, user_agent, organization_id.

    Raises:
        TimeoutError: If login is not completed within timeout.
        ConnectionError: If Chrome cannot be reached.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for Claude Web login. "
            "Install with: pip install 'nanobot-ai[zero-token]' && playwright install chromium"
        )

    # Bypass proxy for local CDP connection
    import os
    old_http = os.environ.pop("http_proxy", None)
    old_https = os.environ.pop("https_proxy", None)
    old_all = os.environ.pop("all_proxy", None)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(chrome_cdp_url)
    except Exception as exc:
        await pw.stop()
        raise ConnectionError(
            f"Cannot connect to Chrome at {chrome_cdp_url}. "
            "Start Chrome with: google-chrome --remote-debugging-port=9222"
        ) from exc
    finally:
        if old_http:
            os.environ["http_proxy"] = old_http
        if old_https:
            os.environ["https_proxy"] = old_https
        if old_all:
            os.environ["all_proxy"] = old_all

    try:
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        # Navigate to login page
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        logger.info("Opened Claude login page. Waiting for user to log in...")

        # Poll for sessionKey cookie
        session_key = ""
        elapsed = 0
        poll_interval = 2

        while elapsed < timeout_s:
            cookies = await context.cookies("https://claude.ai")
            for c in cookies:
                if c["name"] == "sessionKey" and c["value"]:
                    session_key = c["value"]
                    break
            if session_key:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if not session_key:
            raise TimeoutError(
                f"Login timed out after {timeout_s}s. "
                "Please complete the login in the browser and try again."
            )

        # Capture user agent
        user_agent = await page.evaluate("navigator.userAgent")

        # Build full cookie string
        all_cookies = await context.cookies("https://claude.ai")
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies)

        # Try to get organization ID
        org_id = ""
        try:
            await page.goto("https://claude.ai", wait_until="networkidle")
            result = await page.evaluate(
                """async () => {
                    const resp = await fetch('https://claude.ai/api/organizations', {
                        headers: { 'Content-Type': 'application/json' },
                    });
                    if (!resp.ok) return [];
                    return await resp.json();
                }"""
            )
            if result and isinstance(result, list) and len(result) > 0:
                org_id = result[0].get("uuid", "")
        except Exception:
            logger.warning("Could not fetch organization ID (will be fetched at runtime)")

        creds = {
            "session_key": session_key,
            "cookie": cookie_str,
            "user_agent": user_agent,
            "organization_id": org_id,
        }

        save_credentials(creds)
        return creds

    finally:
        await browser.close()
        await pw.stop()

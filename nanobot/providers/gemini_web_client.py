"""Gemini Web (zero-token) browser client using Playwright CDP.

Uses pure DOM simulation: types message into Gemini's input box, submits,
then polls the DOM until the response stabilises.
Streaming is simulated via the expose_function bridge.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class GeminiWebClientConfig:
    """Configuration for the Gemini Web browser client."""

    cookie: str = ""
    user_agent: str = ""
    chrome_cdp_url: str = "http://127.0.0.1:9222"
    attach_only: bool = True
    base_url: str = "https://gemini.google.com"


class GeminiWebClient:
    """Browser-based client for interacting with gemini.google.com.

    Uses Playwright CDP to attach to Chrome, then simulates DOM interaction
    (type message → click send → poll DOM for response).
    """

    _MAX_RECONNECT_ATTEMPTS = 3

    def __init__(self, config: GeminiWebClientConfig) -> None:
        self._config = config
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._connected = False
        self._pending_streams: dict[str, asyncio.Queue[str | None]] = {}
        self._stream_bridge_registered = False

    async def ensure_browser(self) -> None:
        """Ensure browser is connected and on gemini.google.com."""
        if self._connected and self._page:
            try:
                await self._page.evaluate("1")
                if "gemini.google.com" not in self._page.url:
                    self._connected = False
                else:
                    return
            except Exception:
                self._connected = False

        for attempt in range(1, self._MAX_RECONNECT_ATTEMPTS + 1):
            try:
                await self._connect()
                return
            except Exception as exc:
                logger.warning(
                    "Gemini browser connect {}/{} failed: {}",
                    attempt, self._MAX_RECONNECT_ATTEMPTS, exc,
                )
                if attempt < self._MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(1)
                else:
                    raise ConnectionError(
                        f"Failed to connect to Chrome at {self._config.chrome_cdp_url} "
                        "after 3 attempts"
                    ) from exc

    async def _connect(self) -> None:
        """Connect to Chrome via CDP and navigate to gemini.google.com."""
        import os

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is required for zero-token mode. "
                "Install with: pip install 'nanobot-ai[zero-token]' && playwright install chromium"
            )

        saved = {k: os.environ.pop(k, None) for k in ("http_proxy", "https_proxy", "all_proxy")}
        try:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self._config.chrome_cdp_url,
            )
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

        contexts = self._browser.contexts
        context = contexts[0] if contexts else await self._browser.new_context()

        pages = context.pages
        gemini_page = next((p for p in pages if "gemini.google.com" in p.url), None)
        if gemini_page:
            self._page = gemini_page
            logger.debug("Reusing existing gemini.google.com page: {}", gemini_page.url)
        else:
            self._page = await context.new_page()
            await self._page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
            logger.debug("Navigated new page to gemini.google.com/app")

        # Inject cookies if provided
        if self._config.cookie:
            cookies = [
                {
                    "name": c.strip().split("=", 1)[0],
                    "value": c.strip().split("=", 1)[1] if "=" in c else "",
                    "domain": ".google.com",
                    "path": "/",
                }
                for c in self._config.cookie.split(";") if c.strip()
            ]
            valid = [c for c in cookies if c["name"]]
            if valid:
                try:
                    await context.add_cookies(valid)
                except Exception as e:
                    logger.warning("Failed to inject Gemini cookies: {}", e)

        self._connected = True
        self._stream_bridge_registered = False
        logger.info(
            "Connected to Chrome via CDP at {} (gemini.google.com)", self._config.chrome_cdp_url
        )

    async def _setup_streaming_bridge(self) -> None:
        """Expose _nanobotGeminiStreamDelta to JS once per page."""
        if self._stream_bridge_registered:
            return

        async def _on_delta(request_id: str, delta: str | None) -> None:
            q = self._pending_streams.get(request_id)
            if q is not None:
                await q.put(delta)

        try:
            await self._page.expose_function("_nanobotGeminiStreamDelta", _on_delta)
            self._stream_bridge_registered = True
        except Exception as exc:
            logger.debug("Gemini streaming bridge already registered: {}", exc)
            self._stream_bridge_registered = True

    _DOM_SEND_AND_POLL_JS = r"""
async ([message, reqId, maxWaitMs, pollIntervalMs]) => {
    // 1. Find input element
    const inputSelectors = [
        '[placeholder*="Gemini"]', '[placeholder*="Ask"]',
        '[data-placeholder*="Gemini"]', '[contenteditable="true"]',
        'div[role="textbox"]', 'textarea', '[aria-label*="message"]',
        '[aria-label*="prompt"]',
    ];
    let inputEl = null;
    for (const sel of inputSelectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) { inputEl = el; break; }
    }
    if (!inputEl) { throw new Error('Gemini DOM: input element not found'); }

    // 2. Type message
    inputEl.focus();
    if (inputEl.tagName === 'TEXTAREA' || inputEl.tagName === 'INPUT') {
        inputEl.value = message;
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
        inputEl.innerText = message;
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
        inputEl.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // 3. Find and click send button
    const sendSelectors = [
        'button[aria-label*="Send"]', 'button[aria-label*="send"]',
        'button[aria-label*="提交"]', 'button[aria-label*="发送"]',
        'button[type="submit"]', 'button[data-icon="send"]',
        'button[data-testid*="send"]', '.send-button',
        '[aria-label*="Send message"]',
    ];
    let sendBtn = null;
    for (const sel of sendSelectors) {
        const btn = document.querySelector(sel);
        if (btn && !btn.disabled) { sendBtn = btn; break; }
    }
    if (sendBtn) {
        sendBtn.click();
    } else {
        // Fallback: Enter key
        inputEl.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
        }));
    }

    // 4. Poll DOM for response
    const skipTexts = ['Ask Gemini', 'Enter a prompt', '输入提示', 'Gemini', 'Type something'];
    const isSkip = t => t.length < 30 || skipTexts.some(s => t.includes(s));

    const sidebarRoot = document.querySelector('[aria-label*="对话"], [class*="sidebar"], nav');
    const notInSidebar = el => !sidebarRoot?.contains(el);
    const inputRoot = inputEl.closest('form') ?? inputEl.closest('[class*="input"]') ?? inputEl.parentElement?.parentElement;
    const notInInput = el => !inputRoot?.contains(el);
    const main = document.querySelector('main') ?? document.querySelector('[role="main"]') ?? document.body;
    const scoped = main === document.body ? document : main;

    let lastText = '', stableCount = 0, lastEmitted = '';
    for (let elapsed = 0; elapsed < maxWaitMs; elapsed += pollIntervalMs) {
        await new Promise(r => setTimeout(r, pollIntervalMs));
        let text = '';
        const modelSelectors = [
            '[data-message-author="model"]', '[data-sender="model"]',
            '[class*="model-turn"]', '[class*="modelResponse"]',
            'article', '[class*="markdown"]',
        ];
        for (const sel of modelSelectors) {
            const els = scoped.querySelectorAll(sel);
            for (let i = els.length - 1; i >= 0; i--) {
                const el = els[i];
                if (!notInSidebar(el) || !notInInput(el)) continue;
                const t = (el.textContent || '').replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
                if (t.length >= 30 && !isSkip(t)) { text = t; break; }
            }
            if (text) break;
        }

        const stopBtn = document.querySelector('[aria-label*="Stop"], [aria-label*="stop"]');
        if (text && text !== lastText) {
            const delta = text.slice(lastEmitted.length);
            if (delta) {
                await _nanobotGeminiStreamDelta(reqId, delta);
                lastEmitted = text;
            }
            lastText = text; stableCount = 0;
        } else if (text) {
            stableCount++;
            if (!stopBtn && stableCount >= 2) break;
        }
    }
    await _nanobotGeminiStreamDelta(reqId, null);
    return lastText;
}
"""

    async def send_message_stream(
        self,
        prompt: str,
        on_delta: Any | None = None,
    ) -> str:
        """Send a message via DOM simulation, stream incremental chunks via on_delta.

        Returns the full response text.
        """
        await self.ensure_browser()
        await self._setup_streaming_bridge()

        request_id = str(uuid.uuid4())[:12]
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending_streams[request_id] = queue

        max_wait_ms = 120_000
        poll_interval_ms = 2_000

        try:
            js_task = asyncio.create_task(
                asyncio.wait_for(
                    self._page.evaluate(
                        self._DOM_SEND_AND_POLL_JS,
                        [prompt, request_id, max_wait_ms, poll_interval_ms],
                    ),
                    timeout=max_wait_ms / 1000 + 10,
                )
            )

            full_text = ""
            while True:
                try:
                    delta = await asyncio.wait_for(
                        queue.get(), timeout=max_wait_ms / 1000 + 15
                    )
                except asyncio.TimeoutError:
                    js_task.cancel()
                    raise TimeoutError("Gemini DOM polling timed out")
                if delta is None:
                    break
                full_text += delta
                if on_delta:
                    await on_delta(delta)

            await js_task
            return full_text
        finally:
            self._pending_streams.pop(request_id, None)

    async def send_message(self, prompt: str) -> str:
        """Send a message (non-streaming). Returns full response text."""
        return await self.send_message_stream(prompt)

    async def close(self) -> None:
        """Close browser connection."""
        for attr in ("_browser", "_playwright"):
            obj = getattr(self, attr, None)
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        self._browser = None
        self._playwright = None
        self._page = None
        self._connected = False

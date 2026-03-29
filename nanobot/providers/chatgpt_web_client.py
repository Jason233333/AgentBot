"""ChatGPT Web (zero-token) browser client using Playwright CDP.

Handles browser lifecycle, cookie injection, and HTTP requests to chatgpt.com
via page.evaluate (runs fetch inside the authenticated browser context).

Two request paths:
  1. Sentinel API — uses oaistatic.com JS to obtain anti-bot tokens, then
     calls /backend-api/conversation directly. Preferred path.
  2. DOM fallback — types message into UI, waits for response via DOM polling.
     Used when sentinel returns 403 or fails to load.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class ChatGPTWebClientConfig:
    """Configuration for the ChatGPT Web browser client."""

    access_token: str = ""
    cookie: str = ""
    user_agent: str = ""
    chrome_cdp_url: str = "http://127.0.0.1:9222"
    attach_only: bool = True
    base_url: str = "https://chatgpt.com"


class ChatGPTWebClient:
    """Browser-based client for interacting with chatgpt.com.

    Uses Playwright to connect to a Chrome instance via CDP, then performs
    HTTP requests inside the browser context (inheriting authenticated cookies).
    """

    _MAX_RECONNECT_ATTEMPTS = 3

    def __init__(self, config: ChatGPTWebClientConfig) -> None:
        self._config = config
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._connected = False
        self._pending_streams: dict[str, asyncio.Queue[str | None]] = {}
        self._stream_bridge_registered = False

    async def ensure_browser(self) -> None:
        """Ensure browser connection is alive and on chatgpt.com domain."""
        if self._connected and self._page:
            try:
                await self._page.evaluate("1")
                if "chatgpt.com" not in self._page.url:
                    logger.warning(
                        "Page navigated away from chatgpt.com ({}), reconnecting...",
                        self._page.url,
                    )
                    self._connected = False
                else:
                    return
            except Exception:
                logger.warning("Browser connection lost, reconnecting...")
                self._connected = False

        for attempt in range(1, self._MAX_RECONNECT_ATTEMPTS + 1):
            try:
                await self._connect()
                return
            except Exception as exc:
                logger.warning(
                    "Browser connect attempt {}/{} failed: {}",
                    attempt, self._MAX_RECONNECT_ATTEMPTS, exc,
                )
                if attempt < self._MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(1)
                else:
                    raise ConnectionError(
                        f"Failed to connect to Chrome at {self._config.chrome_cdp_url} "
                        f"after {self._MAX_RECONNECT_ATTEMPTS} attempts. "
                        "Make sure Chrome is running with --remote-debugging-port=9222"
                    ) from exc

    async def _connect(self) -> None:
        """Connect to Chrome via CDP and navigate to chatgpt.com."""
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
        chatgpt_page = next((p for p in pages if "chatgpt.com" in p.url), None)
        if chatgpt_page:
            self._page = chatgpt_page
            logger.debug("Reusing existing chatgpt.com page: {}", chatgpt_page.url)
        else:
            self._page = await context.new_page()
            await self._page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            logger.debug("Navigated new page to chatgpt.com")

        # Inject cookies if provided
        cookie_str = self._config.cookie or (
            f"__Secure-next-auth.session-token={self._config.access_token}"
            if self._config.access_token else ""
        )
        if cookie_str and not cookie_str.startswith("{"):
            raw_cookies = [
                {
                    "name": c.strip().split("=", 1)[0],
                    "value": c.strip().split("=", 1)[1] if "=" in c else "",
                    "domain": ".chatgpt.com",
                    "path": "/",
                }
                for c in cookie_str.split(";") if c.strip()
            ]
            valid = [c for c in raw_cookies if c["name"]]
            if valid:
                await context.add_cookies(valid)

        self._connected = True
        self._stream_bridge_registered = False
        logger.info(
            "Connected to Chrome via CDP at {} (chatgpt.com)", self._config.chrome_cdp_url
        )

    async def _setup_streaming_bridge(self) -> None:
        """Expose _nanobotGPTStreamDelta to JS once per page."""
        if self._stream_bridge_registered:
            return

        async def _on_delta(request_id: str, delta: str | None) -> None:
            q = self._pending_streams.get(request_id)
            if q is not None:
                await q.put(delta)

        try:
            await self._page.expose_function("_nanobotGPTStreamDelta", _on_delta)
            self._stream_bridge_registered = True
        except Exception as exc:
            logger.debug("GPT streaming bridge already registered: {}", exc)
            self._stream_bridge_registered = True

    # ------------------------------------------------------------------
    # JS snippet: sentinel auth + conversation API call
    # ------------------------------------------------------------------

    _SENTINEL_FETCH_JS = r"""
async ([body, reqId, timeoutMs]) => {
    const baseHeaders = (accessToken, deviceId) => ({
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'oai-device-id': deviceId,
        'oai-language': 'en-US',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    });

    async function getSession() {
        const r = await fetch('https://chatgpt.com/api/auth/session', { credentials: 'include' });
        return r.ok ? r.json() : null;
    }

    async function tryFetchWithSentinel(accessToken, deviceId) {
        const scripts = Array.from(document.scripts);
        const assetSrc = scripts.map(s => s.src).find(s => s?.includes('oaistatic.com') && s.endsWith('.js'));
        if (!assetSrc) { return { error: 'oaistatic script not found' }; }
        try {
            const g = await import(/* @vite-ignore */ assetSrc);
            if (typeof g.bk !== 'function' || typeof g.fX !== 'function') {
                return { error: `Sentinel asset missing bk/fX` };
            }
            const z = await g.bk();
            const turnstileKey = z?.turnstile?.bx ?? z?.turnstile?.dx;
            if (!turnstileKey) { return { error: 'missing turnstile key' }; }
            const r = await g.bi(turnstileKey);
            let arkose = null;
            try { arkose = await g.bl?.getEnforcementToken?.(z); } catch {}
            let proof = null;
            try { proof = await g.bm?.getEnforcementToken?.(z); } catch {}
            const extraHeaders = await g.fX(z, arkose, r, proof, null);
            const headers = { ...baseHeaders(accessToken, deviceId), ...(typeof extraHeaders === 'object' ? extraHeaders : {}) };
            const res = await fetch('https://chatgpt.com/backend-api/conversation', {
                method: 'POST', headers, body: JSON.stringify(body), credentials: 'include',
            });
            return { res };
        } catch(e) {
            return { error: `sentinel failed: ${e.message}` };
        }
    }

    const session = await getSession();
    const accessToken = session?.accessToken;
    const deviceId = session?.oaiDeviceId ?? crypto.randomUUID?.() ?? Math.random().toString(36).slice(2);

    const sentinelResult = await tryFetchWithSentinel(accessToken, deviceId);
    const res = sentinelResult.res ?? await fetch('https://chatgpt.com/backend-api/conversation', {
        method: 'POST',
        headers: baseHeaders(accessToken, deviceId),
        body: JSON.stringify(body),
        credentials: 'include',
    });

    if (!res.ok) {
        const errText = await res.text();
        await _nanobotGPTStreamDelta(reqId, null);
        throw new Error(`ChatGPT API ${res.status}: ${errText.slice(0, 300)}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let conversationId = null;
    let parentMessageId = null;
    let accumulatedContent = '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const dataStr = line.slice(6).trim();
                if (dataStr === '[DONE]') continue;
                try {
                    const data = JSON.parse(dataStr);
                    if (data.conversation_id) conversationId = data.conversation_id;
                    if (data.message?.id) parentMessageId = data.message.id;
                    const role = data.message?.author?.role ?? data.message?.role;
                    if (role && role !== 'assistant') continue;
                    const rawPart = data.message?.content?.parts?.[0];
                    const content = typeof rawPart === 'string' ? rawPart
                        : (rawPart && typeof rawPart === 'object' && 'text' in rawPart ? rawPart.text : null);
                    if (typeof content === 'string' && content) {
                        const delta = content.slice(accumulatedContent.length);
                        if (delta) {
                            accumulatedContent = content;
                            await _nanobotGPTStreamDelta(reqId, delta);
                        }
                    }
                } catch {}
            }
        }
    } finally {
        clearTimeout(timer);
    }

    await _nanobotGPTStreamDelta(reqId, null);
    return { conversationId, parentMessageId };
}
"""

    # Poll-only JS: called after input+send are handled by Playwright native APIs.
    _DOM_POLL_JS = r"""
async ([reqId, maxWaitMs, pollIntervalMs]) => {
    const clean = t => t.replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
    // Multiple selectors to handle ChatGPT DOM variations across versions
    const responseSelectors = [
        '[data-message-author-role="assistant"]',
        '[data-message-role="assistant"]',
        'article[data-testid*="conversation-turn"]:not([data-message-author-role="user"])',
        '.agent-turn',
        '[class*="agent-turn"]',
    ];

    const getLatestText = () => {
        for (const sel of responseSelectors) {
            const els = document.querySelectorAll(sel);
            for (let i = els.length - 1; i >= 0; i--) {
                const t = clean(els[i].textContent || '');
                if (t.length > 5) return t;
            }
        }
        return '';
    };

    let lastText = '';
    let stableCount = 0;
    let lastEmitted = '';
    for (let elapsed = 0; elapsed < maxWaitMs; elapsed += pollIntervalMs) {
        await new Promise(r => setTimeout(r, pollIntervalMs));
        const text = getLatestText();
        const stopBtn = document.querySelector(
            '[data-testid="stop-button"], button[aria-label*="Stop generating"]'
        );
        if (text && text !== lastText) {
            const delta = text.slice(lastEmitted.length);
            if (delta) {
                await _nanobotGPTStreamDelta(reqId, delta);
                lastEmitted = text;
            }
            lastText = text;
            stableCount = 0;
        } else if (text) {
            stableCount++;
            if (!stopBtn && stableCount >= 3) break;
        }
    }
    if (!lastText) { throw new Error('ChatGPT DOM: no response detected within timeout'); }
    await _nanobotGPTStreamDelta(reqId, null);
    return lastText;
}
"""

    def _build_request_body(
        self,
        message: str,
        model: str,
        conversation_id: str | None,
        parent_message_id: str | None,
    ) -> dict[str, Any]:
        """Build the ChatGPT API request body."""
        message_id = str(uuid.uuid4())
        body: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": message_id,
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [message]},
                }
            ],
            "parent_message_id": parent_message_id or str(uuid.uuid4()),
            "model": model or "gpt-4o",
            "timezone_offset_min": 0,
            "history_and_training_disabled": False,
            "conversation_mode": {"kind": "primary_assistant", "plugin_ids": None},
            "force_paragen": False,
            "force_use_sse": True,
        }
        if conversation_id and conversation_id != "new":
            body["conversation_id"] = conversation_id
        return body

    async def send_message_stream(
        self,
        prompt: str,
        model: str = "gpt-4o",
        conversation_id: str | None = None,
        parent_message_id: str | None = None,
        on_delta: Any | None = None,
    ) -> tuple[str, str | None, str | None]:
        """Send a message and stream response chunks via on_delta callback.

        Returns:
            Tuple of (full_text, new_conversation_id, new_parent_message_id).
        """
        await self.ensure_browser()
        await self._setup_streaming_bridge()

        request_id = str(uuid.uuid4())[:12]
        body = self._build_request_body(prompt, model, conversation_id, parent_message_id)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending_streams[request_id] = queue

        sse_timeout_ms = 300_000

        try:
            js_task = asyncio.create_task(
                asyncio.wait_for(
                    self._page.evaluate(self._SENTINEL_FETCH_JS, [body, request_id, sse_timeout_ms]),
                    timeout=sse_timeout_ms / 1000 + 10,
                )
            )

            full_text = ""
            while True:
                try:
                    delta = await asyncio.wait_for(queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    js_task.cancel()
                    raise TimeoutError("No GPT chunk received for 60s")
                if delta is None:
                    break
                full_text += delta
                if on_delta:
                    await on_delta(delta)

            result = await js_task
            new_conv_id = result.get("conversationId") if isinstance(result, dict) else None
            new_parent_id = result.get("parentMessageId") if isinstance(result, dict) else None
            return full_text, new_conv_id, new_parent_id

        except Exception as exc:
            # Try DOM fallback if API failed (e.g. 403 anti-bot)
            err_msg = str(exc).lower()
            if "403" in err_msg or "sentinel" in err_msg or "oaistatic" in err_msg:
                logger.warning(
                    "[chatgpt-zero] API failed ({}), trying DOM fallback", str(exc)[:80]
                )
                queue2: asyncio.Queue[str | None] = asyncio.Queue()
                self._pending_streams[request_id] = queue2
                try:
                    # Navigate to a fresh conversation so this DOM request has no
                    # prior context from the same browser tab.
                    await self._page.goto(
                        "https://chatgpt.com/", wait_until="domcontentloaded"
                    )
                    await asyncio.sleep(1.5)
                    # Re-register streaming bridge (persists across navigations but
                    # flag needs resetting so we confirm it's still live).
                    self._stream_bridge_registered = False
                    await self._setup_streaming_bridge()

                    # Step 1: type message via real key events (Lexical requires this)
                    input_loc = self._page.locator("#prompt-textarea").first
                    try:
                        await input_loc.wait_for(state="visible", timeout=5000)
                    except Exception:
                        input_loc = self._page.locator(
                            "div[contenteditable='true']"
                        ).first
                        await input_loc.wait_for(state="visible", timeout=5000)
                    # Click to focus, then type via page.keyboard so Lexical fires events
                    await input_loc.click()
                    await asyncio.sleep(0.2)
                    await self._page.keyboard.type(prompt, delay=10)
                    await asyncio.sleep(0.8)

                    # Step 2: wait for send button to be enabled, then click
                    send_sel = (
                        "button[data-testid='send-button'], "
                        "#composer-submit-button, "
                        "button[aria-label='Send prompt']"
                    )
                    send_loc = self._page.locator(send_sel).first
                    # Wait up to 5s for button to become enabled after fill()
                    await self._page.wait_for_selector(
                        (
                            "button[data-testid='send-button']:not([disabled]),"
                            "button[data-testid='send-button']:not([aria-disabled='true']),"
                            "#composer-submit-button:not([disabled]),"
                            "button[aria-label='Send prompt']:not([disabled])"
                        ),
                        state="visible",
                        timeout=5000,
                    )

                    # Submitting on new-chat page triggers a URL navigation to /c/<id>.
                    # Use expect_navigation so polling JS starts on the settled page.
                    url_before = self._page.url
                    needs_navigation = "/c/" not in url_before
                    if needs_navigation:
                        async with self._page.expect_navigation(
                            wait_until="domcontentloaded", timeout=12000
                        ):
                            await send_loc.click()
                    else:
                        await send_loc.click()
                        await asyncio.sleep(0.5)

                    # Re-setup bridge after possible navigation
                    self._stream_bridge_registered = False
                    await self._setup_streaming_bridge()

                    # Step 3: poll for response via JS (incremental delta streaming)
                    dom_task = asyncio.create_task(
                        self._page.evaluate(self._DOM_POLL_JS, [request_id, 90000, 2000])
                    )
                    full_text = ""
                    while True:
                        queue_get = asyncio.create_task(queue2.get())
                        try:
                            done, _ = await asyncio.wait(
                                {queue_get, dom_task},
                                timeout=120.0,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        except asyncio.CancelledError:
                            queue_get.cancel()
                            dom_task.cancel()
                            raise
                        if not done:
                            queue_get.cancel()
                            dom_task.cancel()
                            raise TimeoutError("ChatGPT DOM: no response in 120s")
                        if dom_task in done and not dom_task.cancelled():
                            task_exc = dom_task.exception()
                            if task_exc:
                                queue_get.cancel()
                                raise task_exc
                        if not queue_get.done():
                            queue_get.cancel()
                            break
                        delta = queue_get.result()
                        if delta is None:
                            break
                        full_text += delta
                        if on_delta:
                            await on_delta(delta)
                    if not dom_task.done():
                        await dom_task
                    return full_text, None, None
                except Exception as dom_exc:
                    raise RuntimeError(
                        f"ChatGPT both API and DOM failed. API: {exc}. DOM: {dom_exc}"
                    ) from dom_exc
            raise
        finally:
            self._pending_streams.pop(request_id, None)

    async def send_message(
        self,
        prompt: str,
        model: str = "gpt-4o",
        conversation_id: str | None = None,
        parent_message_id: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        """Send a message (non-streaming). Returns (text, conv_id, parent_msg_id)."""
        return await self.send_message_stream(
            prompt=prompt,
            model=model,
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
        )

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

    @staticmethod
    def _map_model(model: str) -> str:
        """Map nanobot model names to ChatGPT model identifiers."""
        model_lower = model.lower().replace("-", "").replace("_", "")
        mapping = {
            "gpt4o": "gpt-4o",
            "gpt4omini": "gpt-4o-mini",
            "gpt4turbo": "gpt-4-turbo",
            "gpt4": "gpt-4",
            "gpt35turbo": "gpt-3.5-turbo",
            "o1": "o1",
            "o1mini": "o1-mini",
            "o3mini": "o3-mini",
        }
        for prefix in ("openai/", "chatgpt/", "gpt_web/"):
            pfx = prefix.replace("-", "").replace("_", "")
            if model_lower.startswith(pfx):
                model_lower = model_lower[len(pfx):]
                break
        for key, value in mapping.items():
            if key in model_lower:
                return value
        return model

"""Claude Web browser client using Playwright CDP.

Handles browser lifecycle, cookie injection, and HTTP requests to claude.ai
via page.evaluate (runs fetch inside the authenticated browser context).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ClaudeWebClientConfig:
    """Configuration for the Claude Web browser client."""

    session_key: str = ""
    cookie: str = ""
    user_agent: str = ""
    organization_id: str = ""
    chrome_cdp_url: str = "http://127.0.0.1:9222"
    attach_only: bool = True
    base_url: str = "https://claude.ai"


class ClaudeWebClient:
    """Browser-based client for interacting with claude.ai.

    Uses Playwright to connect to a Chrome instance via CDP, then performs
    HTTP requests inside the browser context (inheriting authenticated cookies).
    """

    _MAX_RECONNECT_ATTEMPTS = 3

    def __init__(self, config: ClaudeWebClientConfig) -> None:
        self._config = config
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._connected = False

    async def ensure_browser(self) -> None:
        """Ensure browser connection is alive and on claude.ai domain.

        Reconnects if the page is gone or has navigated away from claude.ai
        (same-origin requirement for fetch() calls via page.evaluate).
        """
        if self._connected and self._page:
            try:
                await self._page.evaluate("1")
                if "claude.ai" not in self._page.url:
                    logger.warning(
                        "Page navigated away from claude.ai ({}), reconnecting...",
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
        """Connect to Chrome via CDP."""
        import os

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is required for zero-token mode. "
                "Install with: pip install 'nanobot-ai[zero-token]' && playwright install chromium"
            )

        # Bypass proxy for local CDP connection
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

        # Use existing context (inherits cookies from user's Chrome session)
        contexts = self._browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = await self._browser.new_context()

        # Find or create a page on claude.ai domain so fetch() runs same-origin
        # (cross-origin fetch from a non-claude.ai page is blocked by CORS).
        pages = context.pages
        claude_page = next(
            (p for p in pages if "claude.ai" in p.url), None
        )
        if claude_page:
            self._page = claude_page
            logger.debug("Reusing existing claude.ai page: {}", claude_page.url)
        else:
            self._page = await context.new_page()
            await self._page.goto("https://claude.ai/new", wait_until="domcontentloaded")
            logger.debug("Navigated new page to claude.ai/new")

        # Inject cookies if provided
        if self._config.session_key:
            await context.add_cookies([{
                "name": "sessionKey",
                "value": self._config.session_key,
                "domain": ".claude.ai",
                "path": "/",
            }])

        self._connected = True
        logger.info("Connected to Chrome via CDP at {}", self._config.chrome_cdp_url)

    async def get_organization_id(self) -> str:
        """Fetch the user's organization ID from claude.ai API."""
        if self._config.organization_id:
            return self._config.organization_id

        await self.ensure_browser()
        result = await self._page.evaluate(
            """async () => {
                const resp = await fetch('https://claude.ai/api/organizations', {
                    headers: { 'Content-Type': 'application/json' },
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
                return await resp.json();
            }"""
        )

        if result and isinstance(result, list) and len(result) > 0:
            org_id = result[0].get("uuid", "")
            self._config.organization_id = org_id
            logger.info("Got organization ID: {}", org_id[:8] + "...")
            return org_id

        raise ValueError("Could not retrieve organization ID from claude.ai")

    async def create_conversation(self, model: str = "claude-sonnet-4-6") -> str:
        """Create a new conversation and return its UUID."""
        await self.ensure_browser()
        org_id = await self.get_organization_id()

        # Map model names to claude.ai model identifiers
        web_model = self._map_model(model)

        result = await self._page.evaluate(
            """async ([orgId, model]) => {
                const resp = await fetch(
                    `https://claude.ai/api/organizations/${orgId}/chat_conversations`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ uuid: crypto.randomUUID(), name: '', model }),
                    }
                );
                if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
                return await resp.json();
            }""",
            [org_id, web_model],
        )

        conv_id = result.get("uuid", "")
        if not conv_id:
            raise ValueError(f"Failed to create conversation: {result}")
        logger.info("Created conversation: {}", conv_id[:8] + "...")
        return conv_id

    async def send_message(
        self,
        conversation_id: str,
        prompt: str,
        model: str = "claude-sonnet-4-6",
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Send a message to a conversation and return the full response text.

        Reads the SSE stream and concatenates all completion text blocks.

        Args:
            conversation_id: UUID of the conversation.
            prompt: The message text to send.
            model: Model identifier.
            attachments: Optional list of file attachments.

        Returns:
            Complete response text from Claude.
        """
        await self.ensure_browser()
        org_id = await self.get_organization_id()
        web_model = self._map_model(model)

        payload = {
            "prompt": prompt,
            "timezone": "Asia/Shanghai",
            "model": web_model,
            "attachments": attachments or [],
        }

        # Use page.evaluate to perform fetch inside browser context
        # This inherits all cookies and auth state.
        # Timeout: 5 minutes for SSE stream (Claude can be slow on long responses).
        _SSE_TIMEOUT_MS = 300_000

        try:
            result = await asyncio.wait_for(
                self._page.evaluate(
                    """async ([orgId, convId, payload, timeoutMs]) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);

                        try {
                            const resp = await fetch(
                                `https://claude.ai/api/organizations/${orgId}/chat_conversations/${convId}/completion`,
                                {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify(payload),
                                    signal: controller.signal,
                                }
                            );
                            if (!resp.ok) {
                                const errText = await resp.text();
                                throw new Error(`HTTP ${resp.status}: ${errText}`);
                            }

                            // Read SSE stream
                            const reader = resp.body.getReader();
                            const decoder = new TextDecoder();
                            let fullText = '';
                            let buffer = '';
                            let sseError = null;

                            while (true) {
                                const { done, value } = await reader.read();
                                if (done) break;

                                buffer += decoder.decode(value, { stream: true });
                                const lines = buffer.split('\\n');
                                buffer = lines.pop() || '';

                                for (const line of lines) {
                                    if (!line.startsWith('data: ')) continue;
                                    try {
                                        const data = JSON.parse(line.slice(6));
                                        if (data.type === 'completion' && data.completion) {
                                            fullText += data.completion;
                                        } else if (data.type === 'content_block_delta'
                                                   && data.delta && data.delta.text) {
                                            fullText += data.delta.text;
                                        } else if (data.type === 'error') {
                                            sseError = data.error || data;
                                        } else if (data.type && !['message_start', 'message_delta',
                                                   'message_stop', 'content_block_start',
                                                   'content_block_stop', 'ping'].includes(data.type)) {
                                            console.log('[zero-token-sse] unhandled event:', JSON.stringify(data).slice(0, 500));
                                        }
                                    } catch (e) {
                                        // Skip non-JSON lines
                                    }
                                }
                            }

                            if (sseError) {
                                throw new Error('SSE error: ' + JSON.stringify(sseError));
                            }

                            return fullText;
                        } finally {
                            clearTimeout(timer);
                        }
                    }""",
                    [org_id, conversation_id, payload, _SSE_TIMEOUT_MS],
                ),
                timeout=_SSE_TIMEOUT_MS / 1000 + 10,  # Python-side timeout slightly longer
            )
        except asyncio.TimeoutError:
            logger.error(
                "Claude Web SSE stream timed out after {}s for conversation {}",
                _SSE_TIMEOUT_MS / 1000, conversation_id[:8] + "...",
            )
            raise TimeoutError(
                f"Claude Web response timed out after {_SSE_TIMEOUT_MS // 1000}s"
            )

        return result or ""

    async def close(self) -> None:
        """Close browser connection."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None
        self._page = None
        self._connected = False

    @staticmethod
    def _map_model(model: str) -> str:
        """Map nanobot model names to claude.ai web model identifiers."""
        model_lower = model.lower().replace("-", "").replace("_", "")

        mapping = {
            "claudeopus45": "claude-opus-4-5",
            "claudeopus46": "claude-opus-4-6",
            "claudesonnet45": "claude-sonnet-4-5",
            "claudesonnet46": "claude-sonnet-4-6",
            "claudehaiku45": "claude-haiku-4-5",
            "claude4opus": "claude-opus-4-5",
            "claude4sonnet": "claude-sonnet-4-5",
            "claude35sonnet": "claude-3-5-sonnet-20241022",
            "claude3opus": "claude-3-opus-20240229",
            "claude3sonnet": "claude-3-sonnet-20240229",
            "claude3haiku": "claude-3-haiku-20240307",
        }

        # Strip common prefixes (remove "anthropic/" or "claude_web/" from model string)
        clean = model_lower
        for prefix in ("anthropic/", "claude_web/"):
            prefix_norm = prefix.replace("-", "").replace("_", "")
            if clean.startswith(prefix_norm):
                clean = clean[len(prefix_norm):]
                break

        for key, value in mapping.items():
            if key in clean:
                return value

        # Default: pass through (claude.ai might accept it directly)
        return model

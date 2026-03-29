# GPT + Gemini Zero-Token Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在现有 Claude zero-token 架构基础上，实现 ChatGPT Web 和 Gemini Web provider，支持用户配置 N 个 provider 的 fallback 链。

**Architecture:** 新增 `chatgpt_web_client.py`/`chatgpt_web_provider.py` 和 `gemini_web_client.py`/`gemini_web_provider.py`，均沿用现有的 XML 工具格式和 hybrid 会话模式（新用户消息 → 全量 prompt；工具结果 → 增量续接）。Config 新增 `ChatGPTWebConfig`/`GeminiWebConfig`，`AgentDefaults` 新增 `zero_providers` 排序列表；新建 `ProviderChain` 支持 N 个 provider 的 fallback 链。

**Tech Stack:** Python, Playwright (CDP), asyncio, pytest-asyncio

---

## 文件一览

| 操作 | 文件                                        |
| ---- | ------------------------------------------- |
| 新建 | `nanobot/providers/chatgpt_web_client.py`   |
| 新建 | `nanobot/providers/chatgpt_web_provider.py` |
| 新建 | `nanobot/providers/gemini_web_client.py`    |
| 新建 | `nanobot/providers/gemini_web_provider.py`  |
| 新建 | `tests/test_chatgpt_web_flow.py`            |
| 新建 | `tests/test_gemini_web_flow.py`             |
| 修改 | `nanobot/config/schema.py`                  |
| 修改 | `nanobot/providers/fallback_provider.py`    |
| 修改 | `nanobot/cli/commands.py`                   |

---

## Task 1: Config schema 扩展

**Files:**

- Modify: `nanobot/config/schema.py`

### Step 1: 在 `ClaudeWebConfig` 后面添加两个新的配置类

在文件末尾找到 `class ClaudeWebConfig(Base):` 块（约第 93 行），在其之后添加：

```python
class ChatGPTWebConfig(Base):
    """ChatGPT Web (zero-token) provider configuration."""

    access_token: str = ""  # __Secure-next-auth.session-token value
    cookie: str = ""        # full cookie string (alternative to access_token)
    user_agent: str = ""
    chrome_cdp_url: str = "http://127.0.0.1:9222"
    attach_only: bool = True


class GeminiWebConfig(Base):
    """Gemini Web (zero-token) provider configuration."""

    cookie: str = ""        # Google account cookies
    user_agent: str = ""
    chrome_cdp_url: str = "http://127.0.0.1:9222"
    attach_only: bool = True
```

### Step 2: 在 `ProvidersConfig` 中加入两个新字段

找到 `claude_web: "ClaudeWebConfig"` 这一行，在其后添加：

```python
    chatgpt_web: "ChatGPTWebConfig" = Field(default_factory=lambda: ChatGPTWebConfig())  # ChatGPT Web (zero-token)
    gemini_web: "GeminiWebConfig" = Field(default_factory=lambda: GeminiWebConfig())    # Gemini Web (zero-token)
```

### Step 3: 在 `AgentDefaults` 中加入 `zero_providers`

找到 `mode: Literal[...]` 这一行，在其后添加：

```python
    zero_providers: list[str] = Field(
        default_factory=lambda: ["claude"],
        description="Ordered list of zero-token providers to try. Options: 'claude', 'gpt', 'gemini'.",
    )
```

### Step 4: 验证无 import 错误

```bash
cd /Users/shing/Documents/Project/AgentBot
uv run python -c "from nanobot.config.schema import Config; print('OK')"
```

预期: `OK`

### Step 5: Commit

```bash
git add nanobot/config/schema.py
git commit -m "feat(zero-token): add ChatGPTWebConfig, GeminiWebConfig, zero_providers to schema"
```

---

## Task 2: ProviderChain (N 个 provider 的 fallback 链)

**Files:**

- Modify: `nanobot/providers/fallback_provider.py`
- Test: `tests/test_zero_token_flow.py` (添加 ProviderChain 测试)

### Step 1: 写失败测试

在 `tests/test_zero_token_flow.py` 末尾添加：

```python
# ---------------------------------------------------------------------------
# 10. ProviderChain (N-provider fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_chain_tries_in_order():
    """ProviderChain: first provider fails → second fails → third succeeds."""
    from nanobot.providers.fallback_provider import ProviderChain

    def make_failing(msg):
        p = AsyncMock(spec=["chat", "get_default_model", "generation"])
        p.chat = AsyncMock(return_value=LLMResponse(content=msg, finish_reason="error"))
        p.get_default_model = MagicMock(return_value="model")
        p.generation = MagicMock()
        return p

    def make_success(msg):
        p = AsyncMock(spec=["chat", "get_default_model", "generation"])
        p.chat = AsyncMock(return_value=LLMResponse(content=msg, finish_reason="stop"))
        p.get_default_model = MagicMock(return_value="model")
        p.generation = MagicMock()
        return p

    p1 = make_failing("error from p1")
    p2 = make_failing("error from p2")
    p3 = make_success("success from p3")

    chain = ProviderChain(providers=[p1, p2, p3])
    resp = await chain.chat(messages=[{"role": "user", "content": "hi"}])

    assert resp.content == "success from p3"
    assert resp.recovered_from_error is True
    p1.chat.assert_awaited_once()
    p2.chat.assert_awaited_once()
    p3.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_chain_all_fail():
    """ProviderChain: all fail → returns last error response."""
    from nanobot.providers.fallback_provider import ProviderChain

    def make_failing(msg):
        p = AsyncMock(spec=["chat", "get_default_model", "generation"])
        p.chat = AsyncMock(return_value=LLMResponse(content=msg, finish_reason="error"))
        p.get_default_model = MagicMock(return_value="model")
        p.generation = MagicMock()
        return p

    chain = ProviderChain(providers=[make_failing("e1"), make_failing("e2")])
    resp = await chain.chat(messages=[{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert "e2" in (resp.content or "")


@pytest.mark.asyncio
async def test_provider_chain_first_succeeds():
    """ProviderChain: first provider succeeds → others not called."""
    from nanobot.providers.fallback_provider import ProviderChain

    p1 = AsyncMock(spec=["chat", "get_default_model", "generation"])
    p1.chat = AsyncMock(return_value=LLMResponse(content="ok", finish_reason="stop"))
    p1.get_default_model = MagicMock(return_value="model")
    p1.generation = MagicMock()
    p2 = AsyncMock(spec=["chat", "get_default_model", "generation"])

    chain = ProviderChain(providers=[p1, p2])
    resp = await chain.chat(messages=[{"role": "user", "content": "hi"}])

    assert resp.content == "ok"
    p2.chat.assert_not_awaited()
```

### Step 2: 运行测试确认失败

```bash
uv run pytest tests/test_zero_token_flow.py::test_provider_chain_tries_in_order -v
```

预期: `FAILED` — `ImportError: cannot import name 'ProviderChain'`

### Step 3: 实现 `ProviderChain`

在 `nanobot/providers/fallback_provider.py` 末尾追加：

```python

class ProviderChain(LLMProvider):
    """Composite provider: tries each provider in order, falling back on failure.

    Each call tries providers[0], then providers[1], ..., until one succeeds
    (finish_reason != "error") or all are exhausted. The fallback is per-call.
    """

    def __init__(self, providers: list[LLMProvider]) -> None:
        super().__init__()
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self._providers = providers

    def get_default_model(self) -> str:
        return self._providers[0].get_default_model()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Try each provider in order, return first success."""
        last_response: LLMResponse | None = None

        for i, provider in enumerate(self._providers):
            try:
                response = await provider.chat(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    tool_choice=tool_choice,
                    **kwargs,
                )
                if response.finish_reason != "error":
                    if i > 0:
                        response.recovered_from_error = True
                    return response

                logger.warning(
                    "Provider #{} ({}) returned error, trying next: {}",
                    i, type(provider).__name__,
                    (response.content or "")[:120],
                )
                last_response = response
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Provider #{} ({}) raised {}, trying next",
                    i, type(provider).__name__, exc,
                )
                last_response = LLMResponse(
                    content=f"Error from {type(provider).__name__}: {exc}",
                    finish_reason="error",
                )

        logger.error("All {} providers in chain failed", len(self._providers))
        return last_response or LLMResponse(
            content="All providers in chain failed", finish_reason="error"
        )
```

### Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_zero_token_flow.py -k "provider_chain" -v
```

预期: 3 个测试全部 `PASSED`

### Step 5: 运行全部测试确认无回归

```bash
uv run pytest tests/test_zero_token_flow.py -v
```

预期: 全部 `PASSED`

### Step 6: Commit

```bash
git add nanobot/providers/fallback_provider.py tests/test_zero_token_flow.py
git commit -m "feat(zero-token): add ProviderChain for N-provider fallback"
```

---

## Task 3: ChatGPT Web Client

**Files:**

- Create: `nanobot/providers/chatgpt_web_client.py`

### Step 1: 创建文件

完整内容如下：

```python
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
import json
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
        logger.info("Connected to Chrome via CDP at {} (chatgpt.com)", self._config.chrome_cdp_url)

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

    _DOM_FALLBACK_JS = r"""
async ([message, reqId, maxWaitMs, pollIntervalMs]) => {
    const inputSelectors = ['#prompt-textarea', 'textarea[placeholder]', 'textarea',
        '[contenteditable="true"][data-placeholder]', "[contenteditable='true']"];
    let inputEl = null;
    for (const sel of inputSelectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) { inputEl = el; break; }
    }
    if (!inputEl) { throw new Error('ChatGPT DOM: input element not found'); }
    inputEl.focus();
    if (inputEl.tagName === 'TEXTAREA' || inputEl.tagName === 'INPUT') {
        inputEl.value = message;
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
        inputEl.textContent = message;
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const sendSelectors = ['#composer-submit-button', 'button[data-testid="send-button"]',
        'button.btn.relative.btn-primary', 'button[aria-label*="Send"]', 'button[type="submit"]',
        'form button[type=submit]'];
    let sendBtn = null;
    for (const sel of sendSelectors) {
        const btn = document.querySelector(sel);
        if (btn && !btn.disabled) { sendBtn = btn; break; }
    }
    if (!sendBtn) { throw new Error('ChatGPT DOM: send button not found'); }
    sendBtn.click();

    let lastText = '';
    let stableCount = 0;
    for (let elapsed = 0; elapsed < maxWaitMs; elapsed += pollIntervalMs) {
        await new Promise(r => setTimeout(r, pollIntervalMs));
        const els = document.querySelectorAll('[data-message-author-role="assistant"]');
        const last = els.length > 0 ? els[els.length - 1] : null;
        const text = last ? (last.textContent || '').replace(/[\u200B-\u200D\uFEFF]/g, '').trim() : '';
        const stopBtn = document.querySelector('button.bg-black .icon-lg, [aria-label*="Stop"]');
        if (text && text !== lastText) { lastText = text; stableCount = 0; }
        else if (text) {
            stableCount++;
            if (!stopBtn && stableCount >= 2) break;
        }
    }
    if (!lastText) { throw new Error('ChatGPT DOM: no response detected'); }
    await _nanobotGPTStreamDelta(reqId, lastText);
    await _nanobotGPTStreamDelta(reqId, null);
    return { conversationId: null, parentMessageId: null };
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

        _SSE_TIMEOUT_MS = 300_000

        try:
            js_task = asyncio.create_task(
                asyncio.wait_for(
                    self._page.evaluate(self._SENTINEL_FETCH_JS, [body, request_id, _SSE_TIMEOUT_MS]),
                    timeout=_SSE_TIMEOUT_MS / 1000 + 10,
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
                logger.warning("[chatgpt-zero] API failed ({}), trying DOM fallback", str(exc)[:80])
                queue2: asyncio.Queue[str | None] = asyncio.Queue()
                self._pending_streams[request_id] = queue2
                try:
                    dom_task = asyncio.create_task(
                        self._page.evaluate(
                            self._DOM_FALLBACK_JS,
                            [prompt, request_id, 90000, 2000],
                        )
                    )
                    full_text = ""
                    while True:
                        delta = await asyncio.wait_for(queue2.get(), timeout=120.0)
                        if delta is None:
                            break
                        full_text += delta
                        if on_delta:
                            await on_delta(delta)
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
```

### Step 2: 验证导入

```bash
uv run python -c "from nanobot.providers.chatgpt_web_client import ChatGPTWebClient; print('OK')"
```

预期: `OK`

### Step 3: Commit

```bash
git add nanobot/providers/chatgpt_web_client.py
git commit -m "feat(zero-token): add ChatGPTWebClient (sentinel auth + DOM fallback)"
```

---

## Task 4: ChatGPT Web Provider + 单元测试

**Files:**

- Create: `nanobot/providers/chatgpt_web_provider.py`
- Create: `tests/test_chatgpt_web_flow.py`

### Step 1: 写失败测试 (`tests/test_chatgpt_web_flow.py`)

```python
"""Integration tests for the ChatGPT Web zero-token provider.

Browser layer is mocked so tests run without a real Chrome instance.
Tests cover: simple chat, tool calls, hybrid conversation mode, error handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.providers.base import LLMResponse
from nanobot.providers.chatgpt_web_provider import ChatGPTWebProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """ChatGPTWebProvider with mocked browser client."""
    p = ChatGPTWebProvider(default_model="gpt-4o")
    return p


def _mock_client(provider: ChatGPTWebProvider, response_text: str,
                 conv_id: str = "gpt-conv-001", parent_id: str = "msg-001") -> None:
    """Patch the internal client to return a canned response."""
    provider._client.send_message = AsyncMock(
        return_value=(response_text, conv_id, parent_id)
    )
    provider._client.ensure_browser = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Simple conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat(provider):
    """Web returns plain text → LLMResponse with content, no tool_calls."""
    _mock_client(provider, "Hello from GPT!")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi there"},
    ]
    resp = await provider.chat(messages)

    assert resp.content == "Hello from GPT!"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    provider._client.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. Tool call response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_response(provider):
    """Web returns XML tool_call → parsed ToolCallRequests."""
    web_response = (
        "Let me read that.\n"
        '<tool_call id="tc1" name="read_file">{"path": "/tmp/test.txt"}</tool_call>'
    )
    _mock_client(provider, web_response)

    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read /tmp/test.txt"},
    ]
    resp = await provider.chat(messages, tools=tools)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "/tmp/test.txt"}

    # Tool definitions injected in prompt
    call_args = provider._client.send_message.call_args
    prompt = call_args[0][0] if call_args[0] else call_args.kwargs.get("prompt", "")
    assert "read_file" in prompt
    assert "Tool Use Instructions" in prompt


# ---------------------------------------------------------------------------
# 3. Hybrid: tool result continuation reuses conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation_reuses_conversation(provider):
    """Tool continuation reuses existing conversation."""
    _mock_client(provider, "File contains: hello world")

    provider._conversations["test-key"] = {
        "conv_id": "gpt-conv-existing",
        "parent_msg_id": "msg-existing",
    }

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "Reading...",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/tmp/test.txt"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "read_file", "content": "hello world"},
    ]
    resp = await provider.chat(messages, session_key="test-key")

    assert resp.content == "File contains: hello world"

    # Should pass existing conv_id to client
    call_kwargs = provider._client.send_message.call_args.kwargs
    assert call_kwargs.get("conversation_id") == "gpt-conv-existing"

    # Incremental prompt: only tool result, no system prompt
    prompt = call_kwargs.get("prompt", "")
    assert "<tool_response" in prompt
    assert "hello world" in prompt
    assert "You are an assistant." not in prompt


@pytest.mark.asyncio
async def test_new_user_message_creates_new_conversation(provider):
    """New user message always creates a new conversation (no conv_id)."""
    _mock_client(provider, "Hello!")

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
    ]
    await provider.chat(messages, session_key="s1")

    call_kwargs = provider._client.send_message.call_args.kwargs
    # conversation_id should be None for new conversation
    assert call_kwargs.get("conversation_id") is None

    # Full prompt includes system content
    prompt = call_kwargs.get("prompt", "")
    assert "You are an assistant." in prompt


@pytest.mark.asyncio
async def test_hybrid_flow_stores_conv_id(provider):
    """After new conversation, conv_id is stored for subsequent tool continuation."""
    _mock_client(provider, "ok", conv_id="gpt-new-001", parent_id="msg-new-001")

    messages = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Do something"},
    ]
    await provider.chat(messages, session_key="s1")

    assert provider._conversations["s1"]["conv_id"] == "gpt-new-001"
    assert provider._conversations["s1"]["parent_msg_id"] == "msg-new-001"


# ---------------------------------------------------------------------------
# 4. Multiple tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_calls(provider):
    """Multiple <tool_call> tags → multiple ToolCallRequests."""
    web_response = (
        "Running tools.\n"
        '<tool_call id="t1" name="search">{"query": "python"}</tool_call>\n'
        '<tool_call id="t2" name="read_file">{"path": "main.py"}</tool_call>'
    )
    _mock_client(provider, web_response)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    resp = await provider.chat(messages)

    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[1].name == "read_file"


# ---------------------------------------------------------------------------
# 5. Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_error_returns_error_response(provider):
    """Browser failure → LLMResponse with finish_reason='error'."""
    provider._client.send_message = AsyncMock(
        side_effect=ConnectionError("Chrome not running")
    )
    provider._client.ensure_browser = AsyncMock()

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "error"
    assert "Chrome not running" in resp.content


@pytest.mark.asyncio
async def test_error_clears_conversation(provider):
    """Error clears conversation mapping for the session."""
    provider._conversations["s1"] = {"conv_id": "old", "parent_msg_id": "p-old"}
    provider._client.send_message = AsyncMock(side_effect=ConnectionError("net error"))
    provider._client.ensure_browser = AsyncMock()

    messages = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Do stuff"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.finish_reason == "error"
    assert "s1" not in provider._conversations


@pytest.mark.asyncio
async def test_clear_session(provider):
    """clear_session removes tracked conversation."""
    provider._conversations["s1"] = {"conv_id": "c1", "parent_msg_id": "p1"}
    provider._conversations["s2"] = {"conv_id": "c2", "parent_msg_id": "p2"}

    provider.clear_session("s1")
    assert "s1" not in provider._conversations
    assert "s2" in provider._conversations

    provider.clear_session()
    assert len(provider._conversations) == 0


# ---------------------------------------------------------------------------
# 6. Message conversion
# ---------------------------------------------------------------------------


class TestChatGPTMessageConversion:
    def setup_method(self):
        self.provider = ChatGPTWebProvider(default_model="gpt-4o")

    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt, _ = self.provider._convert_messages(messages)
        assert "Be helpful." in prompt
        assert "[User]: Hello" in prompt

    def test_tool_defs_injected(self):
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Do something."},
        ]
        tools = [{"type": "function", "function": {"name": "ping", "description": "Ping"}}]
        prompt, _ = self.provider._convert_messages(messages, tools)
        assert "ping" in prompt
        assert "Tool Use Instructions" in prompt

    def test_continuation_only_tool_results(self):
        messages = [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant", "content": "Calling...",
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
        ]
        prompt = ChatGPTWebProvider._convert_continuation(messages)
        assert "result_a" in prompt
        assert "<tool_response" in prompt
        assert "Sys." not in prompt
        assert "Do stuff" not in prompt


# ---------------------------------------------------------------------------
# 7. Streaming
# ---------------------------------------------------------------------------


def _mock_client_streaming(provider: ChatGPTWebProvider, chunks: list[str],
                            conv_id: str = "gpt-conv-stream") -> None:
    """Patch client to simulate streaming."""
    provider._client.ensure_browser = AsyncMock()

    async def _fake_stream(prompt, model, conversation_id, parent_message_id, on_delta):
        full = ""
        for chunk in chunks:
            if on_delta:
                await on_delta(chunk)
            full += chunk
        return full, conv_id, "msg-stream"

    provider._client.send_message_stream = _fake_stream


@pytest.mark.asyncio
async def test_chat_stream_delivers_chunks(provider):
    """chat_stream calls on_content_delta for each chunk."""
    _mock_client_streaming(provider, ["Hello", ", from", " GPT!"])

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Say hello"},
    ]
    received: list[str] = []
    resp = await provider.chat_stream(messages, on_content_delta=lambda d: received.append(d))

    assert received == ["Hello", ", from", " GPT!"]
    assert resp.content == "Hello, from GPT!"
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_tool_call(provider):
    """Streaming tool call response parsed correctly."""
    chunks = [
        "I'll search.\n",
        '<tool_call id="c1" name="search">',
        '{"query": "Tokyo"}',
        "</tool_call>",
    ]
    _mock_client_streaming(provider, chunks)

    messages = [{"role": "user", "content": "Search Tokyo"}]
    resp = await provider.chat_stream(messages)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search"
```

### Step 2: 运行测试确认失败

```bash
uv run pytest tests/test_chatgpt_web_flow.py -v 2>&1 | head -20
```

预期: `ImportError: cannot import name 'ChatGPTWebProvider'`

### Step 3: 实现 `chatgpt_web_provider.py`

```python
"""ChatGPT Web (zero-token) LLM provider.

Mirrors ClaudeWebProvider's hybrid mode:
- New user message → new ChatGPT conversation (full prompt with system + history + tools)
- Tool continuation → reuse existing conversation (incremental tool results only)

Message format: same XML tool format as ClaudeWebProvider (reuses xml_tool_parser).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.chatgpt_web_client import ChatGPTWebClient, ChatGPTWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class ChatGPTWebProvider(LLMProvider):
    """LLM provider that uses chatgpt.com Web interface via browser automation.

    Hybrid conversation management:
    - New user message → no conversation_id (new thread), send full prompt
    - Tool continuation (last msg role="tool", existing session) → reuse thread,
      send only tool results

    Conversation tracking: {session_key → {"conv_id": str, "parent_msg_id": str}}
    """

    _MAX_HISTORY_TURNS = 10

    def __init__(
        self,
        access_token: str = "",
        cookie: str = "",
        user_agent: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "gpt-4o",
        workspace: Path | str | None = None,
    ) -> None:
        super().__init__()
        self._default_model = default_model
        self._client = ChatGPTWebClient(ChatGPTWebClientConfig(
            access_token=access_token,
            cookie=cookie,
            user_agent=user_agent,
            chrome_cdp_url=chrome_cdp_url,
            attach_only=attach_only,
        ))
        # session_key → {"conv_id": str | None, "parent_msg_id": str | None}
        self._conversations: dict[str, dict[str, str | None]] = {}

    def get_default_model(self) -> str:
        return self._default_model

    def clear_session(self, key: str | None = None) -> None:
        """Clear tracked conversation for the given session key."""
        if key and key in self._conversations:
            del self._conversations[key]
            logger.debug("[gpt-zero] cleared conversation for session {}", key)
        elif not key:
            self._conversations.clear()
            logger.debug("[gpt-zero] cleared all conversations")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Stream a response via ChatGPT Web."""
        model = model or self._default_model
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            session = self._conversations.get(session_key)
            is_continuation = (
                messages
                and messages[-1].get("role") == "tool"
                and session is not None
            )

            if is_continuation:
                conv_id = session["conv_id"]
                parent_msg_id = session["parent_msg_id"]
                prompt = self._convert_continuation(messages)
                attachments: list[dict[str, Any]] = []
            else:
                conv_id = None
                parent_msg_id = None
                prompt, attachments = self._convert_messages(messages, tools)

            async def _on_delta(delta: str) -> None:
                if on_content_delta:
                    result = on_content_delta(delta)
                    import asyncio
                    if asyncio.iscoroutine(result):
                        await result

            gpt_model = ChatGPTWebClient._map_model(model)
            full_text, new_conv_id, new_parent_id = await self._client.send_message_stream(
                prompt=prompt,
                model=gpt_model,
                conversation_id=conv_id,
                parent_message_id=parent_msg_id,
                on_delta=_on_delta,
            )

            # Update conversation tracking
            self._conversations[session_key] = {
                "conv_id": new_conv_id or conv_id,
                "parent_msg_id": new_parent_id or parent_msg_id,
            }

            return self._parse_response(full_text)
        except Exception as exc:
            logger.exception("ChatGPT Web stream request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(content=f"Error calling ChatGPT Web: {exc}", finish_reason="error")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request via ChatGPT Web."""
        model = model or self._default_model
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            session = self._conversations.get(session_key)
            is_continuation = (
                messages
                and messages[-1].get("role") == "tool"
                and session is not None
            )

            if is_continuation:
                conv_id = session["conv_id"]
                parent_msg_id = session["parent_msg_id"]
                prompt = self._convert_continuation(messages)
                logger.info(
                    "[gpt-zero] continuation conv={} ({} chars)",
                    (conv_id or "")[:8] + "...", len(prompt),
                )
            else:
                conv_id = None
                parent_msg_id = None
                prompt, _ = self._convert_messages(messages, tools)
                logger.info("[gpt-zero] new conversation ({} chars)", len(prompt))

            gpt_model = ChatGPTWebClient._map_model(model)
            response_text, new_conv_id, new_parent_id = await self._client.send_message(
                prompt=prompt,
                model=gpt_model,
                conversation_id=conv_id,
                parent_message_id=parent_msg_id,
            )

            self._conversations[session_key] = {
                "conv_id": new_conv_id or conv_id,
                "parent_msg_id": new_parent_id or parent_msg_id,
            }

            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("ChatGPT Web request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(
                content=f"Error calling ChatGPT Web: {exc}",
                finish_reason="error",
            )

    # ------------------------------------------------------------------
    # Message conversion (reuses same XML format as ClaudeWebProvider)
    # ------------------------------------------------------------------

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert messages to full XML prompt for ChatGPT Web."""
        parts: list[str] = []

        # 1. System message
        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        parts.append(
            "IMPORTANT: You are the assistant. Never generate lines starting "
            "with '[User]:'. Only output your own response."
        )

        # 2. Tool definitions
        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        # 3. History (trimmed)
        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        last_user_idx = -1
        for i in range(len(history_msgs) - 1, -1, -1):
            if history_msgs[i].get("role") == "user":
                last_user_idx = i
                break

        old_msgs = history_msgs[:last_user_idx] if last_user_idx > 0 else []
        current_msgs = history_msgs[last_user_idx:] if last_user_idx >= 0 else history_msgs

        for msg in old_msgs:
            role = msg.get("role", "")
            if role == "user":
                text = msg.get("content", "")
                if isinstance(text, list):
                    text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
                if text:
                    parts.append(f"[User]: {text}")
            elif role == "assistant":
                content = msg.get("content", "")
                if content:
                    cleaned = self._strip_tool_xml(content)
                    if cleaned:
                        parts.append(f"[Assistant]: {cleaned}")

        for msg in current_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                continue
            elif role == "user":
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                ) if isinstance(content, list) else str(content)
                if text:
                    parts.append(f"[User]: {text}")
            elif role == "assistant":
                if content:
                    parts.append(f"[Assistant]: {content}")
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parts.append(
                        f'<tool_call id="{tc.get("id","")}" name="{fn.get("name","")}">'
                        f'{args}</tool_call>'
                    )
            elif role == "tool":
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result
                ))

        return "\n\n".join(parts), []

    @staticmethod
    def _convert_continuation(messages: list[dict[str, Any]]) -> str:
        """Build incremental prompt with only new tool results."""
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_assistant_idx = i
                break

        parts: list[str] = []
        start = last_assistant_idx + 1 if last_assistant_idx >= 0 else 0
        for msg in messages[start:]:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result
                ))

        return "\n\n".join(parts)

    def _parse_response(self, response_text: str) -> LLMResponse:
        """Parse ChatGPT Web response text into LLMResponse."""
        if not response_text:
            return LLMResponse(content="", finish_reason="stop")

        clean_text, tool_calls = parse_tool_calls(response_text)
        if tool_calls:
            return LLMResponse(
                content=clean_text or None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )
        return LLMResponse(content=clean_text, finish_reason="stop")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    import re as _re
    _TOOL_XML_RE = _re.compile(
        r"<tool_(?:response|call)\b[^>]*>[\s\S]*?</tool_(?:response|call)>",
        _re.IGNORECASE,
    )

    @classmethod
    def _strip_tool_xml(cls, text: str) -> str:
        import re
        cleaned = re.sub(
            r"<tool_(?:response|call)\b[^>]*>[\s\S]*?</tool_(?:response|call)>",
            "", text, flags=re.IGNORECASE,
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    @staticmethod
    def _limit_history_turns(
        messages: list[dict[str, Any]], max_turns: int,
    ) -> list[dict[str, Any]]:
        if max_turns <= 0:
            return messages
        user_count = 0
        cut = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_count += 1
                if user_count > max_turns:
                    cut = i + 1
                    break
        return messages[cut:]

    @staticmethod
    def _get_session_key(messages: list[dict[str, Any]]) -> str:
        for msg in messages:
            if msg.get("role") == "system":
                return str(hash(msg.get("content", "")[:200]))
        return "default"

    async def close(self) -> None:
        await self._client.close()
```

### Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_chatgpt_web_flow.py -v
```

预期: 全部 `PASSED`

### Step 5: Commit

```bash
git add nanobot/providers/chatgpt_web_provider.py tests/test_chatgpt_web_flow.py
git commit -m "feat(zero-token): add ChatGPTWebProvider with e2e tests"
```

---

## Task 5: Gemini Web Client

**Files:**

- Create: `nanobot/providers/gemini_web_client.py`

### Step 1: 创建文件

```python
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
                logger.warning("Gemini browser connect {}/{} failed: {}", attempt, self._MAX_RECONNECT_ATTEMPTS, exc)
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
        logger.info("Connected to Chrome via CDP at {} (gemini.google.com)", self._config.chrome_cdp_url)

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

        _MAX_WAIT_MS = 120_000
        _POLL_INTERVAL_MS = 2_000

        try:
            js_task = asyncio.create_task(
                asyncio.wait_for(
                    self._page.evaluate(
                        self._DOM_SEND_AND_POLL_JS,
                        [prompt, request_id, _MAX_WAIT_MS, _POLL_INTERVAL_MS],
                    ),
                    timeout=_MAX_WAIT_MS / 1000 + 10,
                )
            )

            full_text = ""
            while True:
                try:
                    delta = await asyncio.wait_for(queue.get(), timeout=_MAX_WAIT_MS / 1000 + 15)
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
```

### Step 2: 验证导入

```bash
uv run python -c "from nanobot.providers.gemini_web_client import GeminiWebClient; print('OK')"
```

预期: `OK`

### Step 3: Commit

```bash
git add nanobot/providers/gemini_web_client.py
git commit -m "feat(zero-token): add GeminiWebClient (DOM simulation)"
```

---

## Task 6: Gemini Web Provider + 单元测试

**Files:**

- Create: `nanobot/providers/gemini_web_provider.py`
- Create: `tests/test_gemini_web_flow.py`

### Step 1: 写失败测试 (`tests/test_gemini_web_flow.py`)

```python
"""Integration tests for the Gemini Web zero-token provider.

Browser layer is mocked so tests run without a real Chrome instance.
Gemini uses DOM simulation (no direct API access).
"""

import pytest
from unittest.mock import AsyncMock

from nanobot.providers.gemini_web_provider import GeminiWebProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """GeminiWebProvider with mocked browser client."""
    return GeminiWebProvider(default_model="gemini-2.5-pro")


def _mock_client(provider: GeminiWebProvider, response_text: str) -> None:
    """Patch the internal client to return a canned response."""
    provider._client.send_message = AsyncMock(return_value=response_text)
    provider._client.ensure_browser = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Simple conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat(provider):
    """DOM simulation returns plain text → LLMResponse."""
    _mock_client(provider, "Hello from Gemini!")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi"},
    ]
    resp = await provider.chat(messages)

    assert resp.content == "Hello from Gemini!"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    provider._client.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. First message sends full history; new user message re-sends full history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_message_sends_full_prompt(provider):
    """First message in a new session sends the full prompt (system + history)."""
    _mock_client(provider, "ok")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    await provider.chat(messages, session_key="s1")

    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "You are a helpful assistant." in sent_prompt
    assert "[User]: Hello" in sent_prompt


@pytest.mark.asyncio
async def test_new_user_message_in_existing_session_sends_only_last(provider):
    """After session exists, new user message sends only the last user message."""
    _mock_client(provider, "ok")
    provider._conversations["s1"] = True  # mark session as established

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Now do something else"},
    ]
    await provider.chat(messages, session_key="s1")

    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "Now do something else" in sent_prompt
    # System prompt should NOT be re-sent for subsequent user messages
    assert "You are a helpful assistant." not in sent_prompt


# ---------------------------------------------------------------------------
# 3. Tool call response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_response(provider):
    """DOM returns XML tool_call → parsed ToolCallRequests."""
    web_response = (
        "I'll search.\n"
        '<tool_call id="tc1" name="web_search">{"query": "python"}</tool_call>'
    )
    _mock_client(provider, web_response)

    tools = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Search for python"},
    ]
    resp = await provider.chat(messages, tools=tools)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "web_search"
    assert resp.tool_calls[0].arguments == {"query": "python"}


# ---------------------------------------------------------------------------
# 4. Tool result continuation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation_sends_only_tool_result(provider):
    """Tool continuation sends only the tool result (not full history)."""
    _mock_client(provider, "Search results processed.")
    provider._conversations["s1"] = True

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Search python"},
        {
            "role": "assistant", "content": "Searching...",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "python"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "web_search", "content": "search results"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.content == "Search results processed."
    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "<tool_response" in sent_prompt
    assert "search results" in sent_prompt
    # Full system + history should NOT be re-sent for tool continuation
    assert "You are an assistant." not in sent_prompt


# ---------------------------------------------------------------------------
# 5. Multiple tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_calls(provider):
    web_response = (
        "Running.\n"
        '<tool_call id="t1" name="search">{"query": "a"}</tool_call>\n'
        '<tool_call id="t2" name="read">{"path": "b"}</tool_call>'
    )
    _mock_client(provider, web_response)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    resp = await provider.chat(messages)

    assert len(resp.tool_calls) == 2


# ---------------------------------------------------------------------------
# 6. Session management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_message_sets_session_flag(provider):
    """After first message, session flag is set to True."""
    _mock_client(provider, "ok")

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    await provider.chat(messages, session_key="s1")
    assert provider._conversations.get("s1") is True


@pytest.mark.asyncio
async def test_clear_session(provider):
    provider._conversations["s1"] = True
    provider._conversations["s2"] = True

    provider.clear_session("s1")
    assert "s1" not in provider._conversations
    assert "s2" in provider._conversations

    provider.clear_session()
    assert len(provider._conversations) == 0


# ---------------------------------------------------------------------------
# 7. Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_error_returns_error_response(provider):
    provider._client.send_message = AsyncMock(side_effect=ConnectionError("Chrome not running"))
    provider._client.ensure_browser = AsyncMock()

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "error"
    assert "Chrome not running" in resp.content


@pytest.mark.asyncio
async def test_error_clears_session(provider):
    provider._conversations["s1"] = True
    provider._client.send_message = AsyncMock(side_effect=RuntimeError("DOM fail"))
    provider._client.ensure_browser = AsyncMock()

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "t", "content": "r"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.finish_reason == "error"
    assert "s1" not in provider._conversations


# ---------------------------------------------------------------------------
# 8. Message conversion
# ---------------------------------------------------------------------------


class TestGeminiMessageConversion:
    def setup_method(self):
        self.provider = GeminiWebProvider(default_model="gemini-2.5-pro")

    def test_full_prompt_has_system_and_user(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt = self.provider._build_full_prompt(messages)
        assert "Be helpful." in prompt
        assert "[User]: Hello" in prompt

    def test_full_prompt_includes_tool_defs(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
        ]
        tools = [{"type": "function", "function": {"name": "ping", "description": "Ping"}}]
        prompt = self.provider._build_full_prompt(messages, tools)
        assert "ping" in prompt
        assert "Tool Use Instructions" in prompt

    def test_continuation_only_tool_results(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "name": "t", "content": "result"},
        ]
        prompt = GeminiWebProvider._build_continuation_prompt(messages)
        assert "result" in prompt
        assert "<tool_response" in prompt
        assert "sys" not in prompt

    def test_last_user_message_only(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "last message"},
        ]
        prompt = self.provider._build_last_user_prompt(messages)
        assert "last message" in prompt
        assert "sys" not in prompt


# ---------------------------------------------------------------------------
# 9. Streaming
# ---------------------------------------------------------------------------


def _mock_client_streaming(provider: GeminiWebProvider, chunks: list[str]) -> None:
    provider._client.ensure_browser = AsyncMock()

    async def _fake_stream(prompt, on_delta=None):
        full = ""
        for chunk in chunks:
            if on_delta:
                await on_delta(chunk)
            full += chunk
        return full

    provider._client.send_message_stream = _fake_stream


@pytest.mark.asyncio
async def test_chat_stream_delivers_chunks(provider):
    _mock_client_streaming(provider, ["Hello", " from", " Gemini!"])

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Say hello"},
    ]
    received: list[str] = []
    resp = await provider.chat_stream(messages, on_content_delta=lambda d: received.append(d))

    assert received == ["Hello", " from", " Gemini!"]
    assert resp.content == "Hello from Gemini!"
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_tool_call(provider):
    chunks = [
        "Searching.\n",
        '<tool_call id="c1" name="search">{"query": "test"}</tool_call>',
    ]
    _mock_client_streaming(provider, chunks)

    messages = [{"role": "user", "content": "Search test"}]
    resp = await provider.chat_stream(messages)

    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].name == "search"
```

### Step 2: 运行测试确认失败

```bash
uv run pytest tests/test_gemini_web_flow.py -v 2>&1 | head -20
```

预期: `ImportError: cannot import name 'GeminiWebProvider'`

### Step 3: 实现 `gemini_web_provider.py`

```python
"""Gemini Web (zero-token) LLM provider.

Conversation management (different from Claude/GPT — DOM-based):
- New session (session_key not in _conversations):
    Build full prompt (system + tools + history), send to Gemini DOM.
    Set _conversations[session_key] = True.
- Tool continuation (last msg is role="tool", session exists):
    Send only tool results (Gemini has prior context in page thread).
- New user message in existing session:
    Send only the last user message (Gemini has context from prior full prompt).

Message format: same XML tool format as ClaudeWebProvider.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.gemini_web_client import GeminiWebClient, GeminiWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class GeminiWebProvider(LLMProvider):
    """LLM provider that uses gemini.google.com via DOM simulation.

    Three message modes:
    - New session: full prompt (system + tools + history)
    - Tool continuation: tool results only
    - Existing session, new user msg: last user message only
    """

    _MAX_HISTORY_TURNS = 10

    def __init__(
        self,
        cookie: str = "",
        user_agent: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "gemini-2.5-pro",
        workspace: Path | str | None = None,
    ) -> None:
        super().__init__()
        self._default_model = default_model
        self._client = GeminiWebClient(GeminiWebClientConfig(
            cookie=cookie,
            user_agent=user_agent,
            chrome_cdp_url=chrome_cdp_url,
            attach_only=attach_only,
        ))
        # session_key → True (session established)
        self._conversations: dict[str, bool] = {}

    def get_default_model(self) -> str:
        return self._default_model

    def clear_session(self, key: str | None = None) -> None:
        if key and key in self._conversations:
            del self._conversations[key]
            logger.debug("[gemini-zero] cleared session {}", key)
        elif not key:
            self._conversations.clear()
            logger.debug("[gemini-zero] cleared all sessions")

    def _choose_prompt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        session_key: str,
    ) -> str:
        """Choose prompt strategy based on session state."""
        has_session = self._conversations.get(session_key, False)
        is_continuation = (
            messages
            and messages[-1].get("role") == "tool"
            and has_session
        )

        if is_continuation:
            return self._build_continuation_prompt(messages)
        elif has_session:
            return self._build_last_user_prompt(messages)
        else:
            return self._build_full_prompt(messages, tools)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        session_key = kwargs.get("session_key", self._get_session_key(messages))
        has_session = self._conversations.get(session_key, False)

        try:
            prompt = self._choose_prompt(messages, tools, session_key)
            logger.info("[gemini-zero] sending prompt ({} chars, session={})", len(prompt), has_session)

            response_text = await self._client.send_message(prompt)
            self._conversations[session_key] = True
            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("Gemini Web request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(content=f"Error calling Gemini Web: {exc}", finish_reason="error")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            prompt = self._choose_prompt(messages, tools, session_key)

            async def _on_delta(delta: str) -> None:
                if on_content_delta:
                    result = on_content_delta(delta)
                    import asyncio
                    if asyncio.iscoroutine(result):
                        await result

            response_text = await self._client.send_message_stream(
                prompt=prompt, on_delta=_on_delta,
            )
            self._conversations[session_key] = True
            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("Gemini Web stream failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(content=f"Error calling Gemini Web: {exc}", finish_reason="error")

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_full_prompt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build full prompt: system + tools + history (for new sessions)."""
        parts: list[str] = []

        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        parts.append(
            "IMPORTANT: You are the assistant. Never generate lines starting "
            "with '[User]:'. Only output your own response."
        )

        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        for msg in history_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                text = content if isinstance(content, str) else (
                    " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                    if isinstance(content, list) else str(content)
                )
                if text:
                    parts.append(f"[User]: {text}")
            elif role == "assistant":
                if content:
                    parts.append(f"[Assistant]: {content}")
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parts.append(
                        f'<tool_call id="{tc.get("id","")}" name="{fn.get("name","")}">'
                        f'{args}</tool_call>'
                    )
            elif role == "tool":
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result,
                ))

        return "\n\n".join(parts)

    @staticmethod
    def _build_continuation_prompt(messages: list[dict[str, Any]]) -> str:
        """Build incremental prompt with only new tool results."""
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_assistant_idx = i
                break

        parts: list[str] = []
        start = last_assistant_idx + 1 if last_assistant_idx >= 0 else 0
        for msg in messages[start:]:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result,
                ))
        return "\n\n".join(parts)

    @staticmethod
    def _build_last_user_prompt(messages: list[dict[str, Any]]) -> str:
        """Extract the last user message for existing sessions."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                return str(content)
        return ""

    def _parse_response(self, response_text: str) -> LLMResponse:
        if not response_text:
            return LLMResponse(content="", finish_reason="stop")
        clean_text, tool_calls = parse_tool_calls(response_text)
        if tool_calls:
            return LLMResponse(content=clean_text or None, tool_calls=tool_calls, finish_reason="tool_calls")
        return LLMResponse(content=clean_text, finish_reason="stop")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _limit_history_turns(
        messages: list[dict[str, Any]], max_turns: int,
    ) -> list[dict[str, Any]]:
        if max_turns <= 0:
            return messages
        user_count = 0
        cut = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_count += 1
                if user_count > max_turns:
                    cut = i + 1
                    break
        return messages[cut:]

    @staticmethod
    def _get_session_key(messages: list[dict[str, Any]]) -> str:
        for msg in messages:
            if msg.get("role") == "system":
                return str(hash(msg.get("content", "")[:200]))
        return "default"

    async def close(self) -> None:
        await self._client.close()
```

### Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_gemini_web_flow.py -v
```

预期: 全部 `PASSED`

### Step 5: 运行全部零 token 测试

```bash
uv run pytest tests/test_zero_token_flow.py tests/test_chatgpt_web_flow.py tests/test_gemini_web_flow.py -v
```

预期: 全部 `PASSED`

### Step 6: Commit

```bash
git add nanobot/providers/gemini_web_provider.py tests/test_gemini_web_flow.py
git commit -m "feat(zero-token): add GeminiWebProvider with e2e tests"
```

---

## Task 7: Commands 集成 + `__init__.py` 导出

**Files:**

- Modify: `nanobot/cli/commands.py`
- Modify: `nanobot/providers/__init__.py`

### Step 1: 在 `commands.py` 中添加 `_make_zero_token_chain`

找到 `_make_claude_web_provider` 函数（约第 379 行），在其之后添加：

```python
def _make_chatgpt_web_provider(config: Config):
    """Create a ChatGPTWebProvider from config."""
    from nanobot.providers.chatgpt_web_provider import ChatGPTWebProvider

    cw = config.providers.chatgpt_web
    return ChatGPTWebProvider(
        access_token=cw.access_token,
        cookie=cw.cookie,
        user_agent=cw.user_agent,
        chrome_cdp_url=cw.chrome_cdp_url,
        attach_only=cw.attach_only,
        default_model=config.agents.defaults.model,
    )


def _make_gemini_web_provider(config: Config):
    """Create a GeminiWebProvider from config."""
    from nanobot.providers.gemini_web_provider import GeminiWebProvider

    gw = config.providers.gemini_web
    return GeminiWebProvider(
        cookie=gw.cookie,
        user_agent=gw.user_agent,
        chrome_cdp_url=gw.chrome_cdp_url,
        attach_only=gw.attach_only,
        default_model=config.agents.defaults.model,
    )


def _make_zero_token_chain(config: Config):
    """Build a ProviderChain from config.agents.defaults.zero_providers order.

    Supported provider names: "claude", "gpt", "gemini".
    Unknown names are skipped with a warning.
    """
    from nanobot.providers.fallback_provider import ProviderChain

    _FACTORIES = {
        "claude": _make_claude_web_provider,
        "gpt": _make_chatgpt_web_provider,
        "gemini": _make_gemini_web_provider,
    }

    zero_providers = config.agents.defaults.zero_providers or ["claude"]
    providers = []
    for name in zero_providers:
        factory = _FACTORIES.get(name)
        if factory is None:
            logger.warning("Unknown zero_providers entry '{}', skipping", name)
            continue
        try:
            providers.append(factory(config))
        except Exception as exc:
            logger.warning("Failed to create '{}' zero-token provider: {}", name, exc)

    if not providers:
        raise RuntimeError(
            "No zero-token providers could be initialized. "
            "Check providers.claude_web / chatgpt_web / gemini_web config."
        )

    if len(providers) == 1:
        return providers[0]
    return ProviderChain(providers=providers)
```

### Step 2: 更新 `mode == "zero"` 分支

找到：

```python
    if mode == "zero":
        provider = _make_claude_web_provider(config)
        console.print("[cyan]Mode: zero-token (Claude Web)[/cyan]")
```

替换为：

```python
    if mode == "zero":
        provider = _make_zero_token_chain(config)
        names = " → ".join(config.agents.defaults.zero_providers or ["claude"])
        console.print(f"[cyan]Mode: zero-token ({names})[/cyan]")
```

### Step 3: 更新 `mode == "normal-zero"` 分支（可选：也支持链式）

找到：

```python
    elif mode == "normal-zero":
        from nanobot.providers.fallback_provider import FallbackProvider
        primary = _make_api_provider(config)
        secondary = _make_claude_web_provider(config)
        provider = FallbackProvider(primary=primary, secondary=secondary)
        console.print("[cyan]Mode: normal-zero (API → Claude Web fallback)[/cyan]")
```

替换为：

```python
    elif mode == "normal-zero":
        from nanobot.providers.fallback_provider import ProviderChain
        primary = _make_api_provider(config)
        zero_chain = _make_zero_token_chain(config)
        provider = ProviderChain(providers=[primary, zero_chain])
        names = " → ".join(config.agents.defaults.zero_providers or ["claude"])
        console.print(f"[cyan]Mode: normal-zero (API → {names})[/cyan]")
```

### Step 4: 验证导入

```bash
uv run python -c "from nanobot.cli.commands import _make_zero_token_chain; print('OK')"
```

预期: `OK`

### Step 5: 运行全部测试确认无回归

```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -30
```

预期: 全部现有测试 `PASSED`，无新失败

### Step 6: Commit

```bash
git add nanobot/cli/commands.py
git commit -m "feat(zero-token): wire zero_providers chain into CLI mode=zero and normal-zero"
```

---

## 验证清单

全部任务完成后执行：

```bash
# 1. 全部单元测试绿色
uv run pytest tests/test_zero_token_flow.py tests/test_chatgpt_web_flow.py tests/test_gemini_web_flow.py -v

# 2. 运行 linter（项目配置的工具）
uv run ruff check nanobot/providers/chatgpt_web_client.py nanobot/providers/chatgpt_web_provider.py \
    nanobot/providers/gemini_web_client.py nanobot/providers/gemini_web_provider.py \
    nanobot/providers/fallback_provider.py nanobot/config/schema.py nanobot/cli/commands.py

# 3. 验证配置解析
uv run python -c "
from nanobot.config.schema import Config
c = Config.model_validate({
    'providers': {'chatgptWeb': {'accessToken': 'test'}, 'geminiWeb': {'cookie': 'test'}},
    'agents': {'defaults': {'mode': 'zero', 'zeroProviders': ['gpt', 'claude', 'gemini']}}
})
print('zeroProviders:', c.agents.defaults.zero_providers)
print('chatgptWeb accessToken:', c.providers.chatgpt_web.access_token)
print('geminiWeb cookie:', c.providers.gemini_web.cookie)
"
```

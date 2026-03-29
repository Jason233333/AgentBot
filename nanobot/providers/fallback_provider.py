"""Fallback LLM provider that delegates to a secondary on per-call failure.

Used in ``normal-zero`` mode: tries the primary (API) provider first,
falls back to the secondary (Claude Web) for that single call if the
primary returns an error.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse


class FallbackProvider(LLMProvider):
    """Composite provider: primary with per-call fallback to secondary.

    Each call first tries ``primary.chat()``. If it raises an exception or
    returns ``finish_reason="error"``, the *same* arguments are forwarded to
    ``secondary.chat()``. The fallback is per-call — subsequent calls still
    try the primary first.
    """

    def __init__(self, primary: LLMProvider, secondary: LLMProvider) -> None:
        super().__init__()
        self.primary = primary
        self.secondary = secondary

    def get_default_model(self) -> str:
        return self.primary.get_default_model()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Try primary provider, fall back to secondary on failure."""
        try:
            response = await self.primary.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
            )
            if response.finish_reason != "error":
                return response

            logger.warning(
                "Primary provider returned error, falling back to secondary: {}",
                (response.content or "")[:120],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Primary provider raised {}, falling back to secondary", exc)

        # Fallback to secondary
        try:
            response = await self.secondary.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
            )
            if response.finish_reason != "error":
                response.recovered_from_error = True
            return response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Secondary provider also failed")
            return LLMResponse(
                content=f"Both providers failed. Secondary error: {exc}",
                finish_reason="error",
            )


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

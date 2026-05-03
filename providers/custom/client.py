"""Custom OpenAI-compatible provider implementation."""

from typing import Any

from loguru import logger

from core.anthropic import ReasoningReplayMode, build_base_request_body
from core.anthropic.conversion import OpenAIConversionError
from providers.base import ProviderConfig
from providers.exceptions import InvalidRequestError
from providers.openai_compat import OpenAIChatTransport


class CustomProvider(OpenAIChatTransport):
    """Generic OpenAI-compatible provider for user-defined endpoints."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        provider_name: str,
        base_url: str,
        configured_models: list[str] | None = None,
    ):
        super().__init__(
            config,
            provider_name=provider_name,
            base_url=base_url,
            api_key=config.api_key,
        )
        self._configured_models = frozenset(configured_models or [])

    async def list_model_ids(self) -> frozenset[str]:
        try:
            upstream = await super().list_model_ids()
        except Exception:
            logger.debug(
                "Custom provider model list fetch failed, using configured models"
            )
            upstream = frozenset()
        return upstream | self._configured_models

    def _build_request_body(
        self, request: Any, thinking_enabled: bool | None = None
    ) -> dict:
        thinking = self._is_thinking_enabled(request, thinking_enabled)
        try:
            return build_base_request_body(
                request,
                reasoning_replay=ReasoningReplayMode.REASONING_CONTENT
                if thinking
                else ReasoningReplayMode.DISABLED,
            )
        except OpenAIConversionError as exc:
            raise InvalidRequestError(str(exc)) from exc

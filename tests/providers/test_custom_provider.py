"""Tests for the custom OpenAI-compatible provider."""

from unittest.mock import MagicMock, patch

import pytest

from config.provider_catalog import PROVIDER_CATALOG
from config.provider_ids import SUPPORTED_PROVIDER_IDS
from providers.custom import CustomProvider
from providers.exceptions import AuthenticationError
from providers.registry import create_provider


def _make_settings(**overrides):
    mock = MagicMock()
    mock.custom_api_key = "test-key"
    mock.custom_base_url = "https://api.example.com/v1"
    mock.custom_provider_name = "MyProvider"
    mock.custom_proxy = ""
    mock.provider_rate_limit = 40
    mock.provider_rate_window = 60
    mock.provider_max_concurrency = 5
    mock.http_read_timeout = 300.0
    mock.http_write_timeout = 10.0
    mock.http_connect_timeout = 10.0
    mock.enable_model_thinking = True
    mock.log_raw_sse_events = False
    mock.log_api_error_tracebacks = False
    for key, value in overrides.items():
        setattr(mock, key, value)
    return mock


def test_custom_provider_in_supported_ids():
    assert "custom" in SUPPORTED_PROVIDER_IDS


def test_custom_descriptor_in_catalog():
    descriptor = PROVIDER_CATALOG["custom"]
    assert descriptor.provider_id == "custom"
    assert descriptor.transport_type == "openai_chat"
    assert descriptor.credential_env is None
    assert descriptor.credential_attr == "custom_api_key"
    assert descriptor.base_url_attr == "custom_base_url"
    assert descriptor.proxy_attr == "custom_proxy"
    assert "chat" in descriptor.capabilities
    assert "streaming" in descriptor.capabilities


def test_create_custom_provider_instantiates_correctly():
    with patch("providers.openai_compat.AsyncOpenAI"):
        provider = create_provider("custom", _make_settings())
    assert isinstance(provider, CustomProvider)


def test_create_custom_provider_uses_provider_name():
    with patch("providers.openai_compat.AsyncOpenAI"):
        provider = create_provider(
            "custom", _make_settings(custom_provider_name="SpecialProvider")
        )
    assert isinstance(provider, CustomProvider)
    assert provider._provider_name == "SpecialProvider"


def test_create_custom_provider_defaults_name_when_empty():
    with patch("providers.openai_compat.AsyncOpenAI"):
        provider = create_provider("custom", _make_settings(custom_provider_name=""))
    assert isinstance(provider, CustomProvider)
    assert provider._provider_name == "Custom"


def test_create_custom_provider_raises_when_base_url_missing():
    with pytest.raises(AuthenticationError, match="CUSTOM_BASE_URL"):
        create_provider("custom", _make_settings(custom_base_url=""))


def test_create_custom_provider_raises_when_base_url_whitespace():
    with pytest.raises(AuthenticationError, match="CUSTOM_BASE_URL"):
        create_provider("custom", _make_settings(custom_base_url="   "))


def test_custom_provider_no_api_key_required():
    """Custom provider must work without an API key (local endpoints)."""
    with patch("providers.openai_compat.AsyncOpenAI"):
        provider = create_provider("custom", _make_settings(custom_api_key=""))
    assert isinstance(provider, CustomProvider)


def test_custom_provider_build_request_body():
    """_build_request_body delegates to build_base_request_body."""
    from unittest.mock import patch as _patch

    with _patch("providers.openai_compat.AsyncOpenAI"):
        provider = create_provider("custom", _make_settings())

    assert isinstance(provider, CustomProvider)

    request = MagicMock()
    request.model = "gpt-4o"
    request.messages = []
    request.max_tokens = 512
    request.system = None
    request.tools = []
    request.stop_sequences = []
    request.thinking = None
    request.temperature = None
    request.top_p = None
    request.top_k = None
    request.metadata = None
    request.stream = True

    with _patch(
        "providers.custom.client.build_base_request_body",
        return_value={"model": "gpt-4o"},
    ) as mock_build:
        body = provider._build_request_body(request, thinking_enabled=False)

    mock_build.assert_called_once()
    assert body == {"model": "gpt-4o"}

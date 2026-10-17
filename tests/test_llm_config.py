from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from src.agent_core import RuntimeNotConfigured
from src.config import GEMINI_OPENAI_BASE_URL, OPENROUTER_BASE_URL, Settings
from src.container import AppContainer
from src.services import ProviderCompatibleChatOpenAI


def settings(tmp_path: Path, **values) -> Settings:
    defaults = {
        "llm_provider": "auto",
        "llm_api_key": "",
        "llm_model": "",
        "llm_base_url": "",
        "openai_api_key": "",
        "openrouter_api_key": "",
        "gemini_api_key": "",
        "google_api_key": "",
        "database_url": "postgresql+psycopg2://moveinmate:example-only@localhost:5432/moveinmate_dev",
        "chroma_persist_dir": str(tmp_path / "chroma"),
        "knowledge_dir": str(Path(__file__).resolve().parents[1] / "docs" / "knowledge"),
    }
    return Settings(_env_file=None, **(defaults | values))


def test_auto_ignores_placeholder_openai_key_and_selects_openrouter(tmp_path):
    configured = settings(
        tmp_path,
        openai_api_key="sk-your-key-here",
        openrouter_api_key="openrouter-secret",
        gemini_api_key="gemini-secret",
    ).resolve_llm()

    assert configured is not None
    assert configured.provider == "openrouter"


def test_auto_selects_gemini_when_it_is_the_only_valid_provider(tmp_path):
    configured = settings(
        tmp_path,
        openai_api_key="<your-openai-key>",
        gemini_api_key="gemini-secret",
    ).resolve_llm()

    assert configured is not None
    assert configured.provider == "gemini"


def test_openrouter_configuration(tmp_path):
    configured = settings(
        tmp_path,
        llm_provider="openrouter",
        openrouter_api_key="openrouter-secret",
        openrouter_model="vendor/model",
    ).resolve_llm()

    assert configured is not None
    assert configured.provider == "openrouter"
    assert configured.base_url == OPENROUTER_BASE_URL
    assert configured.model == "vendor/model"
    assert configured.default_headers == {
        "HTTP-Referer": "http://localhost:8000",
        "X-OpenRouter-Title": "MoveInMate AI",
    }


def test_gemini_prefers_specific_key_and_uses_compatibility_endpoint(tmp_path):
    configured = settings(
        tmp_path,
        llm_provider="gemini",
        gemini_api_key="gemini-secret",
        google_api_key="generic-google-secret",
    ).resolve_llm()

    assert configured is not None
    assert configured.api_key == "gemini-secret"
    assert configured.base_url == GEMINI_OPENAI_BASE_URL
    assert configured.model == "gemini-3.1-flash-lite"
    assert configured.default_headers == {"x-goog-api-client": "moveinmate-ai/1.0.0"}


@pytest.mark.parametrize("provider", ["openai", "openrouter", "gemini"])
def test_explicit_provider_rejects_missing_or_placeholder_key(tmp_path, provider):
    configured = settings(
        tmp_path,
        llm_provider=provider,
        openai_api_key="sk-your-key-here",
        openrouter_api_key="<your-openrouter-key>",
        gemini_api_key="placeholder",
    ).resolve_llm()

    assert configured is None


@pytest.mark.parametrize(
    ("provider", "key_field", "model_field", "model_name", "expected_base_url"),
    [
        (
            "openrouter",
            "openrouter_api_key",
            "openrouter_model",
            "vendor/model",
            OPENROUTER_BASE_URL,
        ),
        (
            "gemini",
            "gemini_api_key",
            "gemini_model",
            "gemini-3.1-flash-lite",
            GEMINI_OPENAI_BASE_URL,
        ),
    ],
)
def test_model_factory_accepts_compatible_provider_config(
    tmp_path, provider, key_field, model_field, model_name, expected_base_url
):
    app = AppContainer(
        settings(
            tmp_path,
            llm_provider=provider,
            **{key_field: "provider-secret", model_field: model_name},
        )
    )

    try:
        model = app._model()
        assert model.model_name == model_name
        assert str(model.openai_api_base) == expected_base_url
        assert model.openai_api_key.get_secret_value() == "provider-secret"
        assert model.use_responses_api is False
        assert app._model().http_client is model.http_client
        assert app._model().http_async_client is model.http_async_client
    finally:
        app.close()


def test_model_factory_fails_without_any_provider(tmp_path):
    app = AppContainer(settings(tmp_path))

    try:
        with pytest.raises(RuntimeNotConfigured, match="No LLM provider"):
            app._model()
    finally:
        app.close()


def test_compatible_model_preserves_gemini_tool_metadata():
    raw_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup_item", "arguments": '{"item_id":"SKU-1"}'},
        "extra_content": {"google": {"thought_signature": "encrypted-signature"}},
    }
    message = AIMessage(
        content="",
        additional_kwargs={"tool_calls": [raw_call]},
        tool_calls=[{"name": "lookup_item", "args": {"item_id": "SKU-1"}, "id": "call-1"}],
    )
    model = ProviderCompatibleChatOpenAI(
        model="provider/model",
        api_key="test-secret",
        base_url="https://provider.example.test/v1",
        use_responses_api=False,
    )

    payload = model._get_request_payload([message])

    assert payload["messages"][0]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "encrypted-signature"}
    }

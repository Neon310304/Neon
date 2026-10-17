import logging
import traceback
from pathlib import Path

import httpx
import pytest

from src.config import LLMConnection, Settings
from src.logging_config import configure_logging
from src.services.ptit_speech import PtitSpeechError, PtitTextToSpeech


def test_private_files_are_excluded_from_git_and_docker_builds():
    root = Path(__file__).resolve().parents[1]
    for filename in (".gitignore", ".dockerignore"):
        patterns = (root / filename).read_text().splitlines()
        for pattern in (
            ".env", ".env.*", ".runtime/", "project-docs/private/", "*.pem", "*.key", "*.p12", "*.pfx",
            ".aws/", ".codex/", ".claude/", ".cursor/", ".gemini/", ".agents/", ".mcp.json",
        ):
            assert pattern in patterns
    docker_patterns = (root / ".dockerignore").read_text().splitlines()
    assert "**/.env" in docker_patterns
    assert "**/.env.*" in docker_patterns


def test_internal_development_notes_stay_local_and_out_of_docker_images():
    root = Path(__file__).resolve().parents[1]
    git_patterns = (root / ".gitignore").read_text().splitlines()
    for pattern in (
        "AGENTS.md", "AGENT.md", "CLAUDE.md", "GEMINI.md",
        "project-docs/AWS_AGENT_TOOLKIT_20261009.md", "project-docs/AWS_DATA_SETTINGS_RESET_*.md",
        "project-docs/AWS_LIVE_*.md", "project-docs/NOTIFICATION_GROUPS_*.md",
        "project-docs/RESIDENT_WORKFLOWS_*.md", "project-docs/TICKET_CORRECTIONS_*.md",
        "project-docs/TICKET_STATUS_UI_*.md", "project-docs/UI_LIQUID_MASCOT_*.md", "project-docs/VOICE_PTIT_*.md",
    ):
        assert pattern in git_patterns
    assert "project-docs/" in (root / ".dockerignore").read_text().splitlines()


def test_private_settings_are_not_in_representations():
    private_fields = (
        "llm_api_key", "llm_base_url", "openai_api_key", "openrouter_api_key",
        "gemini_api_key", "google_api_key", "gemini_tts_api_key", "gemini_tts_base_url",
        "omicx_api_key", "omicx_stt_url", "ptit_tts_url", "ptit_stt_url",
        "database_url", "test_database_url",
    )
    settings = Settings(_env_file=None, **{name: f"private-{name}" for name in private_fields})
    for name in private_fields:
        assert getattr(settings, name) not in repr(settings)
    connection = LLMConnection("custom", "private-key", "example-model", "https://private.example.test")
    assert connection.api_key not in repr(connection)
    assert connection.base_url not in repr(connection)


@pytest.mark.asyncio
async def test_outbound_request_logs_are_not_enabled_even_with_debug(caplog):
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    previous_levels = [logger.level for logger in loggers]
    try:
        with caplog.at_level(logging.DEBUG):
            configure_logging("DEBUG")
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
                await client.get("https://private.example.test/token?api_key=private-token")
            logging.getLogger("moveinmate.test").info("application diagnostic")
            logging.getLogger("httpcore").debug("private transport data")
        assert "application diagnostic" in caplog.text
        assert "private.example.test" not in caplog.text
        assert "private-token" not in caplog.text
        assert "private transport data" not in caplog.text
    finally:
        for logger, previous_level in zip(loggers, previous_levels, strict=True):
            logger.setLevel(previous_level)


@pytest.mark.asyncio
async def test_speech_failure_traceback_does_not_include_upstream_url(monkeypatch):
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            class FailingStream:
                async def __aenter__(self):
                    raise httpx.ConnectError("https://private.example.test?api_key=private-token")

                async def __aexit__(self, *args):
                    return False

            return FailingStream()

    monkeypatch.setattr("src.services.ptit_speech.httpx.AsyncClient", lambda **kwargs: FailingClient())
    with pytest.raises(PtitSpeechError) as raised:
        await PtitTextToSpeech("https://private.example.test/synthesize").synthesize("Xin chào")
    rendered = "".join(traceback.format_exception(raised.value))
    assert "private-token" not in rendered
    assert raised.value.__suppress_context__

import asyncio
from pathlib import Path
from threading import RLock

import httpx

from src.agent_core import AgentRuntime, PackRegistry, RuntimeNotConfigured
from src.config import Settings, get_settings
from src.project_packs import MoveInMatePack
from src.rag import RegulationRetriever
from src.repositories import KnowledgeStore, Store
from src.repositories.settings import load_database_settings
from src.security import LocalAuthService
from src.services import GeminiTextToSpeech, OmicxSpeechToText, ProviderCompatibleChatOpenAI
from src.services.ai_limits import AiUsageGate
from src.services.ptit_speech import PtitSpeechToText, PtitTextToSpeech
from src.services.ticket_corrections import TicketCorrectionAssistant
from src.services.usage_limits import PrivateResultCache
from src.tools import ToolService


class AppContainer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._http_lock = RLock()
        self._http_client = None
        self._http_async_clients = {}
        self._closed = False
        self.store = Store(
            self.settings.require_database_url(),
            pool_size=self.settings.db_pool_size,
            max_overflow=self.settings.db_max_overflow,
            pool_timeout=self.settings.db_pool_timeout_seconds,
            connect_timeout=self.settings.db_connect_timeout_seconds,
        )
        try:
            self._configure(settings is not None)
        except BaseException:
            try:
                retriever = getattr(self, "retriever", None)
                if retriever is not None:
                    retriever.close()
            finally:
                self.store.engine.dispose()
            raise

    def _configure(self, explicit_settings: bool) -> None:
        if explicit_settings:
            self.settings = load_database_settings(self.settings, self.store)
        self.knowledge_store = KnowledgeStore(self.store)
        self.local_auth = LocalAuthService(self.settings, self.store)
        knowledge_dir = Path(self.settings.knowledge_dir)
        if not knowledge_dir.is_dir():
            knowledge_dir = Path(__file__).resolve().parent / "project_packs" / "knowledge"
        self.retriever = RegulationRetriever(
            str(knowledge_dir), self.settings.chroma_persist_dir,
            knowledge_store=self.knowledge_store, include_demo=self.settings.include_demo_knowledge,
        )
        self.tool_service = ToolService(self.store, self.retriever)
        self.correction_assistant = TicketCorrectionAssistant(self.tool_service, self._model)
        self.private_results = PrivateResultCache()
        self.ai_usage = AiUsageGate(self.store, self.settings)
        self.packs = PackRegistry()
        self.packs.register(MoveInMatePack(self.tool_service))
        secrets = tuple(value for name in type(self.settings).model_fields if any(label in name for label in ("password", "api_key", "jwt_secret", "encryption_key"))
                        if isinstance(value := getattr(self.settings, name), str) and len(value) >= 4)
        self.agent = AgentRuntime(self.packs, self._model, reset_generation=self.store.login_reset_generation,
                                  public_streaming=True, speech_redactions=secrets)
        self.speech_to_text = (
            PtitSpeechToText(self.settings.ptit_stt_url) if self.settings.stt_provider == "ptit"
            else OmicxSpeechToText(self.settings.omicx_api_key, self.settings.omicx_stt_url)
        )
        self.text_to_speech = PtitTextToSpeech(self.settings.ptit_tts_url, self.settings.ptit_tts_timeout_seconds) if self.settings.tts_provider == "ptit" else GeminiTextToSpeech(
            self.settings.resolve_gemini_tts_key(),
            self.settings.gemini_tts_model,
            self.settings.gemini_tts_voice,
            self.settings.gemini_tts_base_url,
        )

    def initialize(self) -> None:
        if self._closed:
            raise RuntimeError("container_closed")
        try:
            self._initialize()
        except BaseException:
            try:
                self.retriever.close()
            finally:
                self.store.engine.dispose()
            raise

    def _initialize(self) -> None:
        self.settings.ensure_runtime_dirs()
        self.store.initialize()
        self.local_auth.provision_accounts()
        from src.services.login_reset import available, capture_baseline
        if available(self):
            capture_baseline(self.store, self.settings.local_auth_site_id)
            from src.services.demo_accounts import ensure_default_accounts

            ensure_default_accounts(self)
        self.retriever.initialize()

    def close(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("use_aclose_in_running_event_loop")

    async def aclose(self) -> None:
        with self._http_lock:
            if self._closed:
                return
            self._closed = True
            sync_client, self._http_client = self._http_client, None
            async_clients, self._http_async_clients = self._http_async_clients, {}
        try:
            try:
                results = await asyncio.gather(
                    *(self._close_http_client(loop, client) for loop, client in async_clients.items()),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
            finally:
                closer = getattr(self.text_to_speech, "aclose", None)
                if closer:
                    await closer()
        finally:
            try:
                if sync_client is not None:
                    sync_client.close()
            finally:
                try:
                    self.retriever.close()
                finally:
                    self.store.engine.dispose()

    @staticmethod
    async def _close_http_client(loop, client) -> None:
        current_loop = asyncio.get_running_loop()
        if loop is None or loop is current_loop or loop.is_closed():
            await client.aclose()
        elif loop.is_running():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(client.aclose(), loop))
        else:
            await asyncio.to_thread(loop.run_until_complete, client.aclose())

    def _model(self):
        connection = self.settings.resolve_llm()
        if not connection:
            raise RuntimeNotConfigured("No LLM provider is configured. Set LLM_PROVIDER and its API key.")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._http_lock:
            if self._closed:
                raise RuntimeError("container_closed")
            if self._http_client is None:
                self._http_client = httpx.Client(timeout=self.settings.ai_llm_timeout_seconds)
            if loop not in self._http_async_clients:
                self._http_async_clients[loop] = httpx.AsyncClient(timeout=self.settings.ai_llm_timeout_seconds)
            http_async_client = self._http_async_clients[loop]
        options = {
            "model": connection.model,
            "api_key": connection.api_key,
            "streaming": True,
            "gemini_compatibility": connection.provider == "gemini",
            "usage_gate": self.ai_usage,
            "max_retries": 0,
            "timeout": self.settings.ai_llm_timeout_seconds,
            "http_client": self._http_client,
            "http_async_client": http_async_client,
        }
        if connection.base_url:
            options["base_url"] = connection.base_url
            options["use_responses_api"] = False
        if connection.default_headers:
            options["default_headers"] = connection.default_headers
        return ProviderCompatibleChatOpenAI(**options)

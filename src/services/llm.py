import asyncio
from copy import deepcopy
from hashlib import sha256
from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import Field

from src.services.ai_limits import ai_operation


class ProviderCompatibleChatOpenAI(ChatOpenAI):
    """Keep provider extensions attached to OpenAI-compatible tool calls.

    Some providers add required fields to a tool call. Gemini 3, for example,
    returns ``extra_content.google.thought_signature`` and requires the exact
    value on the next request. The current ChatOpenAI serializer keeps only the
    standard id/type/function fields, so this adapter restores the raw tool call
    after the standard payload has been constructed.
    """

    gemini_compatibility: bool = False
    usage_gate: Any = Field(default=None, exclude=True, repr=False)

    async def _agenerate(self, *args, **kwargs):
        if self.usage_gate is None:
            return await super()._agenerate(*args, **kwargs)
        async with self.usage_gate.call(ai_operation.get()):
            async with asyncio.timeout(self.usage_gate.settings.ai_llm_timeout_seconds):
                return await super()._agenerate(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        if self.usage_gate is None:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
            return
        async with self.usage_gate.call(ai_operation.get()):
            async with asyncio.timeout(self.usage_gate.settings.ai_llm_timeout_seconds):
                async for chunk in super()._astream(*args, **kwargs):
                    yield chunk

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        if self.gemini_compatibility:
            chunk = deepcopy(chunk)
            for choice in chunk.get("choices", []):
                calls = (choice.get("delta") or {}).get("tool_calls") or []
                # Gemini can omit the index for a single streamed function call.
                # LangChain otherwise drops the parsed call and skips execution.
                if len(calls) == 1 and calls[0].get("index") is None:
                    calls[0]["index"] = 0
        return super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        source_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        outgoing_messages = payload.get("messages")
        if not isinstance(outgoing_messages, list):
            return payload

        for source, outgoing in zip(source_messages, outgoing_messages, strict=False):
            if not isinstance(source, AIMessage) or not isinstance(outgoing, dict):
                continue
            raw_calls = source.additional_kwargs.get("tool_calls")
            if isinstance(raw_calls, list) and any(
                isinstance(call, dict) and call.get("extra_content") for call in raw_calls
            ):
                # index belongs to streaming deltas, not completed request calls.
                outgoing["tool_calls"] = [
                    deepcopy({key: value for key, value in call.items() if key != "index"})
                    for call in raw_calls
                ]
                outgoing["content"] = outgoing.get("content") or None
            if self.gemini_compatibility:
                for call in outgoing.get("tool_calls", []):
                    if str(call.get("id", "")).startswith("direct-"):
                        # Only deterministic client-created calls lack a model
                        # signature. Google's documented marker handles these;
                        # real model signatures above must remain unchanged.
                        call.setdefault("extra_content", {}).setdefault("google", {}).setdefault(
                            "thought_signature", "skip_thought_signature_validator",
                        )
            for call in outgoing.get("tool_calls", []):
                call_id = call.get("id")
                if isinstance(call_id, str) and len(call_id) > 64:
                    call["id"] = "call_" + sha256(call_id.encode()).hexdigest()[:40]
        for outgoing in outgoing_messages:
            call_id = outgoing.get("tool_call_id")
            if isinstance(call_id, str) and len(call_id) > 64:
                outgoing["tool_call_id"] = "call_" + sha256(call_id.encode()).hexdigest()[:40]
        return payload

from copy import deepcopy

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages.utils import message_chunk_to_message

from src.services.llm import ProviderCompatibleChatOpenAI


def model(*, gemini=True):
    return ProviderCompatibleChatOpenAI(
        model="gemini-test", api_key="test-only", use_responses_api=False,
        gemini_compatibility=gemini,
    )


def test_direct_tool_result_can_be_sent_to_gemini_without_model_signature():
    messages = [
        HumanMessage(content="Read the regulations"),
        AIMessage(content="", tool_calls=[{
            "id": "direct-local-1", "name": "search_regulations", "args": {"query": "noise"},
        }]),
        ToolMessage(content="Quiet hours start at 22:00", tool_call_id="direct-local-1"),
    ]
    payload = model()._get_request_payload(messages)
    call = payload["messages"][1]["tool_calls"][0]
    assert call["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert "extra_content" not in model(gemini=False)._get_request_payload(messages)["messages"][1]["tool_calls"][0]
    assert messages[1].additional_kwargs == {}


def test_streamed_signature_survives_tool_execution_and_next_turn():
    first = {
        "choices": [{"delta": {"role": "assistant", "tool_calls": [{
            "id": "gemini-call-1", "type": "function",
            "function": {"name": "lookup", "arguments": '{"value":'},
            "extra_content": {"google": {"thought_signature": "opaque-original-signature"}},
        }]}}],
    }
    second = {"choices": [{"delta": {"tool_calls": [{
        "function": {"arguments": '"hello"}'},
    }]}}]}
    untouched = deepcopy(first)
    client = model()
    start = client._convert_chunk_to_generation_chunk(first, AIMessageChunk, None)
    end = client._convert_chunk_to_generation_chunk(second, AIMessageChunk, None)
    answer = message_chunk_to_message((start + end).message)
    assert answer.tool_calls == [{
        "name": "lookup", "args": {"value": "hello"}, "id": "gemini-call-1", "type": "tool_call",
    }]
    messages = [HumanMessage(content="Look it up"), answer,
                ToolMessage(content="found", tool_call_id="gemini-call-1"),
                HumanMessage(content="What next?")]
    call = client._get_request_payload(messages)["messages"][1]["tool_calls"][0]
    assert call["extra_content"]["google"]["thought_signature"] == "opaque-original-signature"
    assert call["function"]["arguments"] == '{"value":"hello"}'
    assert "index" not in call
    assert first == untouched


def test_unsigned_model_call_is_not_disguised_as_client_generated():
    message = AIMessage(content="", tool_calls=[{"id": "model-call", "name": "lookup", "args": {}}])
    call = model()._get_request_payload([message])["messages"][0]["tool_calls"][0]
    assert "extra_content" not in call

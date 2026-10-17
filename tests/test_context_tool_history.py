import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent_core.context import compact_messages


def test_compaction_does_not_replay_orphan_tool_results():
    history = []
    for index in range(6):
        history.extend([
            HumanMessage(content="Check progress"),
            AIMessage(content="", tool_calls=[{"id": f"call-{index}", "name": "progress", "args": {}}]),
            ToolMessage(content="zero", tool_call_id=f"call-{index}"),
            AIMessage(content="Progress is zero"),
        ])
    latest = [
        HumanMessage(content="Check again"),
        AIMessage(content="", tool_calls=[{"id": "latest", "name": "progress", "args": {}}]),
        ToolMessage(content="zero", tool_call_id="latest"),
    ]
    history.extend(latest)
    result, stats = compact_messages(history)
    assert stats.compressed
    assert result[-3:] == latest
    assert isinstance(result[1], HumanMessage)
    calls = set()
    for message in result:
        if isinstance(message, AIMessage):
            calls.update(call["id"] for call in message.tool_calls)
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in calls
    assert len(result) <= 18


def test_small_uncompressed_exchange_keeps_signature_metadata():
    call = AIMessage(content="", additional_kwargs={"provider": "opaque"},
                     tool_calls=[{"id": "one", "name": "progress", "args": {}}])
    messages = [HumanMessage(content="Check"), call, ToolMessage(content="zero", tool_call_id="one")]
    result, stats = compact_messages(messages)
    assert result == messages
    assert not stats.compressed
    assert result[1] is call


def assert_complete_tool_groups(messages):
    pending = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
        else:
            assert not pending
            if isinstance(message, AIMessage):
                pending = {call["id"] for call in message.tool_calls}
    assert not pending


def test_active_human_survives_long_result_under_tiny_budget():
    human = HumanMessage(content="Check the current request", id="latest-human")
    call = AIMessage(content="", tool_calls=[{"id": "active", "name": "lookup", "args": {}}])
    messages = [human, call, ToolMessage(content="result " * 2000, tool_call_id="active")]
    result, stats = compact_messages(messages, max_messages=3, max_chars=400)
    assert result[-1] is human
    assert not any(isinstance(item, ToolMessage) or isinstance(item, AIMessage) and item.tool_calls for item in result)
    assert "Context limit" in result[0].content
    assert stats.context_limited
    assert stats.output_chars <= 400
    assert len(result) <= 3
    assert_complete_tool_groups(result)
    assert len(messages[-1].content) == len("result " * 2000)


def test_tool_arguments_are_budgeted_and_never_rewritten():
    arguments = {"payload": "x" * 100_000}
    human = HumanMessage(content="Check")
    call = AIMessage(content="", tool_calls=[{"id": "big", "name": "lookup", "args": arguments}])
    messages = [human, call, ToolMessage(content="ok", tool_call_id="big")]
    result, stats = compact_messages(messages, max_chars=400)
    assert stats.compressed
    assert stats.input_chars >= len(json.dumps(arguments))
    assert stats.output_chars <= 400
    assert result[-1] is human
    assert "Context limit" in result[0].content
    assert call.tool_calls[0]["args"] == arguments
    assert_complete_tool_groups(result)


def test_tool_argument_cost_is_reported_for_uncompressed_history():
    call = AIMessage(content="", tool_calls=[{"id": "one", "name": "lookup", "args": {"query": "current"}}])
    messages = [HumanMessage(content="Check"), call, ToolMessage(content="ok", tool_call_id="one")]
    result, stats = compact_messages(messages)
    assert result is messages
    assert stats.input_chars == stats.output_chars
    assert stats.output_chars == len("Checkok") + len(json.dumps(call.tool_calls, ensure_ascii=False))


@pytest.mark.parametrize("result_ids", [["one"], ["one", "one"], ["one", "other"], []])
def test_incomplete_batched_exchanges_are_removed_even_without_budget_pressure(result_ids):
    human = HumanMessage(content="Check both")
    call = AIMessage(content="", tool_calls=[
        {"id": "one", "name": "lookup", "args": {"item": 1}},
        {"id": "two", "name": "lookup", "args": {"item": 2}},
    ])
    messages = [human, call, *[ToolMessage(content="ok", tool_call_id=call_id) for call_id in result_ids]]
    result, stats = compact_messages(messages)
    assert stats.compressed
    assert result[-1] is human
    assert "Context limit" in result[0].content
    assert_complete_tool_groups(result)


def test_complete_batch_preserves_arguments_and_metadata_when_clipping_results():
    human = HumanMessage(content="Check both", id="human")
    call = AIMessage(content="", id="call", additional_kwargs={"provider": "signature"}, tool_calls=[
        {"id": "one", "name": "lookup", "args": {"item": 1}},
        {"id": "two", "name": "lookup", "args": {"item": 2}},
    ])
    first = ToolMessage(content="a" * 20_000, tool_call_id="one", name="lookup", id="first", artifact={"source": "synthetic"})
    second = ToolMessage(content="b" * 20_000, tool_call_id="two", name="lookup", id="second", status="error")
    result, stats = compact_messages([human, call, first, second])
    assert_complete_tool_groups(result)
    assert result[-4] is human
    assert result[-3] is call
    assert result[-2].artifact == first.artifact
    assert result[-2].id == "first"
    assert result[-1].status == "error"
    assert result[-1].tool_call_id == "two"
    assert stats.output_chars <= 14_000


@pytest.mark.parametrize("max_messages", [1, 2, 3, 4, 8])
@pytest.mark.parametrize("max_chars", [1, 3, 20, 100, 400])
def test_custom_budgets_keep_latest_multimodal_human_and_complete_exchanges(max_messages, max_chars):
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic", "detail": "high"}}
    human = HumanMessage(content=[{"type": "text", "text": "Latest " * 50}, image], id="latest", additional_kwargs={"provider": "metadata"})
    messages = [HumanMessage(content="Old " * 100), human, AIMessage(content="", tool_calls=[{"id": "one", "name": "lookup", "args": {}}]), ToolMessage(content="result " * 100, tool_call_id="one")]
    result, stats = compact_messages(messages, max_messages=max_messages, max_chars=max_chars)
    retained = next(item for item in result if isinstance(item, HumanMessage) and item.id == "latest")
    assert image in retained.content
    assert retained.additional_kwargs["provider"] == "metadata"
    assert len(result) <= max_messages
    assert stats.output_chars <= max_chars
    assert_complete_tool_groups(result)


def test_structured_text_separators_obey_budget_without_losing_images():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}}
    human = HumanMessage(content=[{"type": "text", "text": "first"}, image, {"type": "text", "text": "second"}, {"type": "text", "text": "third"}])
    result, stats = compact_messages([human], max_messages=1, max_chars=7)
    assert result[0].content[1] == image
    assert stats.output_chars <= 7


def test_active_exchange_takes_priority_over_older_turns():
    older = [HumanMessage(content="old"), AIMessage(content="old answer")]
    active = [HumanMessage(content="current"), AIMessage(content="", tool_calls=[{"id": "one", "name": "lookup", "args": {}}]), ToolMessage(content="ok", tool_call_id="one")]
    result, stats = compact_messages(older + active, max_messages=4)
    assert result[-3:] == active
    assert isinstance(result[0], SystemMessage)
    assert stats.output_messages <= 4
    assert_complete_tool_groups(result)


def test_orphan_result_is_removed_without_losing_latest_human():
    human = HumanMessage(content="current")
    result, stats = compact_messages([human, ToolMessage(content="orphan", tool_call_id="missing")])
    assert result[-1] is human
    assert stats.context_limited
    assert_complete_tool_groups(result)


def test_single_message_fallback_has_explicit_limit_metadata():
    human = HumanMessage(content="current", additional_kwargs={"provider": "original"})
    call = AIMessage(content="", tool_calls=[{"id": "one", "name": "lookup", "args": {}}])
    result, stats = compact_messages([human, call, ToolMessage(content="ok", tool_call_id="one")], max_messages=1)
    assert len(result) == 1
    assert isinstance(result[0], HumanMessage)
    assert result[0].content == "current"
    assert result[0].additional_kwargs["provider"] == "original"
    assert result[0].additional_kwargs["context_limit"] is True
    assert "do not infer results" in result[0].additional_kwargs["context_limit_hint"]
    assert stats.context_limited


def test_structured_string_blocks_are_clipped_with_text_and_images():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}}
    human = HumanMessage(content=["first " * 100, image, {"type": "text", "text": "second " * 100, "provider": "metadata"}, "third " * 100])
    result, stats = compact_messages([human], max_messages=1, max_chars=100)
    assert image in result[0].content
    assert result[0].content[2]["provider"] == "metadata"
    assert stats.output_chars <= 100
    assert len(human.content[0]) == len("first " * 100)


def test_complete_batch_is_removed_atomically_when_message_slots_are_insufficient():
    human = HumanMessage(content="Check both")
    call = AIMessage(content="", tool_calls=[
        {"id": "one", "name": "lookup", "args": {"item": 1}},
        {"id": "two", "name": "lookup", "args": {"item": 2}},
    ])
    messages = [human, call, ToolMessage(content="first", tool_call_id="one"), ToolMessage(content="second", tool_call_id="two")]
    result, stats = compact_messages(messages, max_messages=3)
    assert result[-1] is human
    assert stats.context_limited
    assert_complete_tool_groups(result)
    assert not any(isinstance(item, ToolMessage) for item in result)


def test_raw_provider_tool_arguments_cannot_bypass_budget():
    raw_call = {"id": "one", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"payload": "x" * 100_000})}}
    call = AIMessage(content="", additional_kwargs={"tool_calls": [raw_call]})
    human = HumanMessage(content="Check")
    result, stats = compact_messages([human, call, ToolMessage(content="ok", tool_call_id="one")], max_chars=400)
    assert stats.input_chars > 100_000
    assert stats.output_chars <= 400
    assert stats.context_limited
    assert result[-1] is human
    assert call.additional_kwargs["tool_calls"] == [raw_call]
    assert_complete_tool_groups(result)

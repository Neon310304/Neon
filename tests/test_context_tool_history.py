from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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

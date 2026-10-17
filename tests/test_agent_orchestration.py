import asyncio
import json
from threading import Event

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agent_core import AgentRuntime, PackRegistry
from src.services.observability import AgentEvent


class OrchestrationPack:
    pack_id = "inventory"
    display_name = "Orchestration tests"

    def __init__(self):
        self.effects = []

    def system_prompt(self, language):
        return "Use one tool at a time."

    def tools_for(self, actor_id):
        @tool
        def submit_item(label: str, idempotency_key: str = "") -> str:
            """Submit an inventory request."""
            self.effects.append((actor_id, label))
            return json.dumps({"created": True, "request_id": f"REQ-{len(self.effects)}"})

        return [submit_item]


class PausingModel:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.observed = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        humans = [message.content for message in messages if isinstance(message, HumanMessage)]
        self.observed.append(humans)
        if humans[-1] == "first":
            self.started.set()
            await self.release.wait()
        return AIMessage(content=f"Answer: {humans[-1]}")


def make_runtime(model, **kwargs):
    pack = OrchestrationPack()
    registry = PackRegistry()
    registry.register(pack)
    return AgentRuntime(registry, lambda: model, **kwargs), pack


async def checkpoint_messages(runtime, conversation_id="thread"):
    snapshot = await runtime._graphs[conversation_id].aget_state({"configurable": {"thread_id": conversation_id}})
    return list(snapshot.values["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("second_streams", [False, True])
async def test_concurrent_turns_keep_both_exchanges(second_streams):
    model = PausingModel()
    runtime, _ = make_runtime(model)
    first = asyncio.create_task(runtime.run("inventory", "actor", "thread", "first", "en"))
    await asyncio.wait_for(model.started.wait(), timeout=2)

    async def second_turn():
        if second_streams:
            return [item async for item in runtime.stream("inventory", "actor", "thread", "second", "en")][-1]["answer"]
        return (await runtime.run("inventory", "actor", "thread", "second", "en")).answer

    second = asyncio.create_task(second_turn())
    await asyncio.sleep(0.02)
    assert model.observed == [["first"]]
    model.release.set()
    first_result, second_answer = await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    assert first_result.answer == "Answer: first"
    assert second_answer == "Answer: second"
    assert model.observed == [["first"], ["first", "second"]]
    messages = await checkpoint_messages(runtime)
    assert [item.content for item in messages if isinstance(item, HumanMessage)] == ["first", "second"]
    assert [item.content for item in messages if isinstance(item, AIMessage)] == ["Answer: first", "Answer: second"]
    assert not runtime._active_turns
    assert not runtime._conversation_locks


@pytest.mark.asyncio
async def test_independent_conversations_do_not_block_one_another():
    model = PausingModel()
    runtime, _ = make_runtime(model)
    first = asyncio.create_task(runtime.run("inventory", "actor", "first-thread", "first", "en"))
    await asyncio.wait_for(model.started.wait(), timeout=2)
    try:
        result = await asyncio.wait_for(runtime.run("inventory", "actor", "other-thread", "second", "en"), timeout=2)
        assert result.answer == "Answer: second"
    finally:
        model.release.set()
        await first


class LoopingModel:
    def __init__(self, *, unique=False, batch=False):
        self.calls = 0
        self.unique = unique
        self.batch = batch

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content="", tool_calls=[{
            "id": f"call-{self.calls}-{index}", "name": "submit_item",
            "args": {"label": str(self.calls) if self.unique else "same", "idempotency_key": f"model-key-{self.calls}"},
        } for index in range(3 if self.batch else 1)])


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_identical_tools_are_reused_within_turn_but_not_across_turns(streaming):
    model = LoopingModel()
    runtime, pack = make_runtime(model)
    if streaming:
        result = [item async for item in runtime.stream("inventory", "actor", "thread", "Submit", "en")][-1]
    else:
        result = vars(await runtime.run("inventory", "actor", "thread", "Submit", "en"))

    assert len(pack.effects) == 1
    assert result["requires_human"]
    assert "REQ-1" in result["answer"]
    assert result["tools_used"][-1]["cached"] == "true"
    messages = await checkpoint_messages(runtime)
    assert isinstance(messages[-1], AIMessage)
    assert not messages[-1].tool_calls
    await runtime.run("inventory", "actor", "thread", "Submit again", "en")
    assert len(pack.effects) == 2


@pytest.mark.asyncio
async def test_batched_mutations_are_not_executed():
    model = LoopingModel(batch=True)
    runtime, pack = make_runtime(model)
    result = await runtime.run("inventory", "actor", "thread", "Submit", "en")
    assert not pack.effects
    assert result.requires_human
    assert "not executed" in result.answer
    assert model.calls == 1
    assert all(trace["summary"] == "error" for trace in result.tools_used)


@pytest.mark.asyncio
async def test_read_result_is_refreshed_after_a_mutation_in_same_turn():
    class ReadWritePack(OrchestrationPack):
        read_only_tool_names = {"read_count"}

        def tools_for(self, actor_id):
            @tool
            def read_count() -> str:
                """Read the current number of submitted items."""
                return json.dumps({"count": len(self.effects)})

            return [*super().tools_for(actor_id), read_count]

    class ReadWriteModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 4:
                return AIMessage(content=f"Current count: {json.loads(messages[-1].content)['count']}")
            name, arguments = ("submit_item", {"label": "one"}) if self.calls == 2 else ("read_count", {})
            return AIMessage(content="", tool_calls=[{"id": f"call-{self.calls}", "name": name, "args": arguments}])

    pack = ReadWritePack()
    registry = PackRegistry()
    registry.register(pack)
    runtime = AgentRuntime(registry, ReadWriteModel)
    result = await runtime.run("inventory", "actor", "thread", "Submit and check count", "en")
    assert result.answer == "Current count: 1"
    assert not result.requires_human
    assert len(result.tools_used) == 3
    assert not any(trace.get("cached") for trace in result.tools_used)


@pytest.mark.asyncio
async def test_oversized_tool_exchange_stops_before_reasoning_without_its_result():
    class LargeArgumentModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls > 1:
                pytest.fail("Missing current tool exchange must not be handed back to the model")
            return AIMessage(content="", tool_calls=[{
                "id": "large", "name": "submit_item", "args": {"label": "x" * 100_000},
            }])

    model = LargeArgumentModel()
    runtime, pack = make_runtime(model)
    result = await runtime.run("inventory", "actor", "thread", "Submit", "en")
    assert len(pack.effects) == 1
    assert result.requires_human
    assert result.tools_used == [{"tool": "submit_item", "summary": "created:REQ-1"}]
    assert "REQ-1" in result.answer
    assert isinstance((await checkpoint_messages(runtime))[-1], AIMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_step_limit_keeps_actual_tool_results_and_finishes_checkpoint(streaming):
    runtime, pack = make_runtime(LoopingModel(unique=True))
    if streaming:
        result = [item async for item in runtime.stream("inventory", "actor", "thread", "Submit", "en")][-1]
    else:
        result = vars(await runtime.run("inventory", "actor", "thread", "Submit", "en"))

    assert len(pack.effects) == 6
    assert result["requires_human"]
    assert len(result["tools_used"]) == 6
    assert "REQ-1" in result["answer"]
    assert "REQ-6" in result["answer"]
    messages = await checkpoint_messages(runtime)
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == result["answer"]
    snapshot = await runtime._graphs["thread"].aget_state({"configurable": {"thread_id": "thread"}})
    assert not snapshot.next


@pytest.mark.asyncio
async def test_actor_reset_invalidates_inflight_tools_and_queued_turn():
    class PausingToolModel(PausingModel):
        async def ainvoke(self, messages):
            await super().ainvoke(messages)
            return AIMessage(content="", tool_calls=[{
                "id": "submit", "name": "submit_item", "args": {"label": "must-not-run"},
            }])

    model = PausingToolModel()
    runtime, pack = make_runtime(model)
    first = asyncio.create_task(runtime.run("inventory", "actor", "thread", "first", "en"))
    await asyncio.wait_for(model.started.wait(), timeout=2)
    queued = asyncio.create_task(runtime.run("inventory", "actor", "thread", "second", "en"))
    await asyncio.sleep(0.02)
    runtime.reset_actor("inventory", "actor")
    model.release.set()
    outcomes = await asyncio.wait_for(asyncio.gather(first, queued, return_exceptions=True), timeout=3)
    assert all(isinstance(outcome, ValueError) and str(outcome) == "session_reset" for outcome in outcomes)
    assert not pack.effects
    assert not runtime._graphs
    assert not runtime._active_turns
    assert not runtime._conversation_locks


@pytest.mark.asyncio
async def test_direct_answer_is_saved_before_followup_preparation():
    class DirectPack(OrchestrationPack):
        def __init__(self):
            super().__init__()
            self.prepared_history = []

        def answer_from_request(self, message, language):
            return "Hello!" if message == "hello" else None

        def prepare_user_message(self, message, language, history):
            self.prepared_history = history
            return message

    model = PausingModel()
    pack = DirectPack()
    registry = PackRegistry()
    registry.register(pack)
    runtime = AgentRuntime(registry, lambda: model)
    await runtime.run("inventory", "actor", "thread", "older topic", "en")
    direct = await runtime.run("inventory", "actor", "thread", "hello", "en")
    assert direct.answer == "Hello!"
    assert len(model.observed) == 1
    await runtime.run("inventory", "actor", "thread", "explain more", "en")
    assert pack.prepared_history[-2].content == "hello"
    assert pack.prepared_history[-1].content == "Hello!"
    assert model.observed[-1] == ["older topic", "hello", "explain more"]


@pytest.mark.asyncio
async def test_initial_direct_exchange_is_lazy_and_loaded_by_first_graph_turn():
    class DirectPack(OrchestrationPack):
        def answer_from_request(self, message, language):
            return "Hello!" if message == "hello" else None

    model = PausingModel()
    registry = PackRegistry()
    registry.register(DirectPack())
    runtime = AgentRuntime(registry, lambda: model)
    await runtime.run("inventory", "actor", "thread", "hello", "en")
    assert not runtime._graphs
    assert not model.observed
    await runtime.run("inventory", "actor", "thread", "explain more", "en")
    assert model.observed == [["hello", "explain more"]]
    assert not runtime._direct_history


@pytest.mark.asyncio
async def test_turn_lock_includes_public_generation():
    public_started = asyncio.Event()
    public_release = asyncio.Event()

    class PublicRuntime(AgentRuntime):
        async def _public_events(self, answer, language, public_context, pack):
            public_started.set()
            await public_release.wait()
            yield {"type": "public_done", "answer": "Public answer"}

    model = PausingModel()
    model.release.set()
    registry = PackRegistry()
    registry.register(OrchestrationPack())
    runtime = PublicRuntime(registry, lambda: model, public_streaming=True)

    async def first_turn():
        return [item async for item in runtime.stream("inventory", "actor", "thread", "first", "en")]

    first = asyncio.create_task(first_turn())
    await asyncio.wait_for(public_started.wait(), timeout=2)
    second = asyncio.create_task(runtime.run("inventory", "actor", "thread", "second", "en"))
    await asyncio.sleep(0.02)
    assert model.observed == [["first"]]
    public_release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=3)
    assert (await checkpoint_messages(runtime))[1].content == "Public answer"


@pytest.mark.asyncio
async def test_cancelled_turn_releases_conversation_lock():
    model = PausingModel()
    runtime, _ = make_runtime(model)
    first = asyncio.create_task(runtime.run("inventory", "actor", "thread", "first", "en"))
    await asyncio.wait_for(model.started.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not runtime._conversation_locks
    assert not runtime._active_turns
    result = await asyncio.wait_for(runtime.run("inventory", "actor", "thread", "second", "en"), timeout=2)
    assert result.answer == "Answer: second"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cancelled_write_settles_and_is_recorded_before_next_turn(streaming):
    tool_started = Event()
    tool_release = Event()

    class BlockingPack(OrchestrationPack):
        def tools_for(self, actor_id):
            @tool
            def submit_item(label: str) -> str:
                """Submit an inventory request."""
                if label == "first":
                    tool_started.set()
                    if not tool_release.wait(timeout=5):
                        raise RuntimeError("test_tool_release_timed_out")
                self.effects.append(label)
                return json.dumps({"created": True, "request_id": f"REQ-{len(self.effects)}"})

            return [submit_item]

    class WriteModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Submitted")
            label = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
            return AIMessage(content="", tool_calls=[{"id": f"call-{label}", "name": "submit_item", "args": {"label": label}}])

    pack = BlockingPack()
    registry = PackRegistry()
    registry.register(pack)
    runtime = AgentRuntime(registry, WriteModel)

    async def first_turn():
        if streaming:
            return [item async for item in runtime.stream("inventory", "actor", "thread", "first", "en")]
        return await runtime.run("inventory", "actor", "thread", "first", "en")

    first = asyncio.create_task(first_turn())
    assert await asyncio.wait_for(asyncio.to_thread(tool_started.wait, 3), timeout=4)
    first.cancel()
    second = asyncio.create_task(runtime.run("inventory", "actor", "thread", "second", "en"))
    try:
        await asyncio.sleep(0.05)
        assert not second.done()
        assert not pack.effects
        assert runtime._conversation_locks
    finally:
        tool_release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, timeout=3)
    assert pack.effects == ["first", "second"]
    messages = await checkpoint_messages(runtime)
    first_result = next(item for item in messages if isinstance(item, ToolMessage) and item.tool_call_id == "call-first")
    assert json.loads(first_result.content)["request_id"] == "REQ-1"
    assert any(isinstance(item, AIMessage) and "REQ-1" in item.content for item in messages)
    assert not runtime._conversation_locks


@pytest.mark.asyncio
async def test_tool_permission_is_rechecked_before_cached_reuse():
    class RevokingPack(OrchestrationPack):
        def allowed_tool_names(self, message):
            return set() if self.effects else {"submit_item"}

    pack = RevokingPack()
    registry = PackRegistry()
    registry.register(pack)
    runtime = AgentRuntime(registry, LoopingModel)
    result = await runtime.run("inventory", "actor", "thread", "Submit", "en")
    assert len(pack.effects) == 1
    assert not any(trace.get("cached") for trace in result.tools_used)
    assert result.tools_used[0]["summary"] == "created:REQ-1"
    assert all(trace["summary"] == "error" for trace in result.tools_used[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_scope", ["actor", "project"])
async def test_reset_invalidates_queued_turn_for_direct_only_conversation(reset_scope):
    class DirectPack(OrchestrationPack):
        def answer_from_request(self, message, language):
            return "Hello!" if message == "hello" else None

    registry = PackRegistry()
    registry.register(DirectPack())
    runtime = AgentRuntime(registry, PausingModel)
    stream = runtime.stream("inventory", "actor", "thread", "hello", "en")
    assert (await anext(stream))["type"] == "done"
    assert not runtime._graphs
    queued = asyncio.create_task(runtime.run("inventory", "actor", "thread", "second", "en"))
    await asyncio.sleep(0.02)
    if reset_scope == "actor":
        runtime.reset_actor("inventory", "actor")
    else:
        runtime.reset_project("inventory")
    await stream.aclose()
    with pytest.raises(ValueError, match="session_reset"):
        await queued
    assert not runtime._graphs
    assert not runtime._direct_history
    assert not runtime._conversation_locks


def test_citations_retain_different_retrieved_pages_but_remove_identical_chunks():
    runtime, _ = make_runtime(PausingModel())
    sources = [
        {"source_id": "law", "chunk_id": "law:1", "section": "Page 2", "page_number": 2},
        {"source_id": "law", "chunk_id": "law:2", "section": "Page 3", "page_number": 3},
        {"source_id": "law", "chunk_id": "law:1", "section": "Duplicate", "page_number": 2},
    ]
    result = runtime._build_result([
        HumanMessage(content="Question"),
        ToolMessage(content=json.dumps({"answer_status": "supported", "sources": sources}), tool_call_id="search", name="search"),
        AIMessage(content="Answer"),
    ], AgentEvent("thread", "actor", "test"), "inventory", "en")
    assert [source["section"] for source in result.citations] == ["Page 2", "Page 3"]
    assert [source["page_number"] for source in result.citations] == ["2", "3"]

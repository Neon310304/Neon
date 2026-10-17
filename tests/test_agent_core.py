import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agent_core import AgentRuntime, PackRegistry
from src.project_packs.moveinmate import MoveInMatePack


class DemoPack:
    pack_id = "inventory"
    display_name = "Inventory Demo"

    def system_prompt(self, language: str) -> str:
        return "Use the provided tool and answer briefly."

    def tools_for(self, actor_id: str):
        @tool("lookup_item")
        def lookup_item(item_id: str) -> str:
            """Look up an inventory item."""
            return json.dumps({"item_id": item_id, "status": "available", "actor_id": actor_id})

        return [lookup_item]


class FakeToolCallingModel:
    def __init__(self):
        self.max_human_messages = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.max_human_messages = max(
            self.max_human_messages,
            sum(isinstance(message, HumanMessage) for message in messages),
        )
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Item is available.")
        return AIMessage(
            content="",
            tool_calls=[{"name": "lookup_item", "args": {"item_id": "SKU-1"}, "id": "call-1"}],
        )


class VehicleSlotModel:
    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            if messages[-1].status == "error":
                return AIMessage(content="Mở form đăng ký xe để đính kèm giấy đăng ký. Chưa tạo ticket.")
            payload = json.loads(messages[-1].content)
            return AIMessage(
                content=(
                    f"Đã tạo {payload['request_id']}." if payload.get("created") else "Mở form đăng ký xe để đính kèm giấy đăng ký."
                )
            )
        human_messages = [message for message in messages if isinstance(message, HumanMessage)]
        if len(human_messages) == 1:
            arguments = {"request_type": "vehicle_registration"}
        else:
            arguments = {
                "request_type": "vehicle_registration",
                "vehicle_plate": human_messages[-1].content,
                "idempotency_key": "slot-test-1",
            }
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_management_request",
                    "args": arguments,
                    "id": f"vehicle-call-{len(human_messages)}",
                }
            ],
        )


class MisleadingUpdateModel:
    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Số CCCD không hợp lệ.")
        return AIMessage(content="")


def test_moveinmate_prompt_handles_contextual_typos_safely():
    pack = MoveInMatePack(service=None)
    prompt = pack.system_prompt("vi")

    assert "lỗi chính tả hoặc lỗi gõ" in prompt
    assert "Hỏi lại trước khi gọi tool tạo hoặc cập nhật dữ liệu" in prompt
    assert "người lớn tuổi hoặc người ít dùng công nghệ" in prompt
    assert "Không hiển thị tên tool, mã trạng thái" in prompt
    assert "Không nhận xét rằng người dùng gõ sai" in prompt
    assert 'Luôn xưng "tôi" và gọi người dùng là "bạn"' in prompt
    assert 'không tự đổi sang cách xưng "bác/cháu"' in prompt
    assert "không hỏi lại tên" in prompt
    assert "không hỏi lại phần còn thiếu" in prompt
    assert "không hỏi lại trường đã biết" in prompt
    assert "Không tạo hoặc hiển thị mã giao diện" in prompt
    assert "list_resident_requests" in prompt
    assert "nguồn sự thật hiện tại" in prompt
    assert "không đưa mã phải gọi list_resident_requests" in prompt
    assert "duplicate=true" in prompt
    assert "ngoài phạm vi" in prompt
    assert pack.prepare_user_message("kê", "vi").startswith("Được, tôi đồng ý")
    assert pack.prepare_user_message("OKÊ!", "vi").startswith("Được, tôi đồng ý")
    assert pack.prepare_user_message("KỆ ĐI!", "vi").startswith("Thôi, tôi muốn dừng")
    assert pack.prepare_user_message("kê khai xe", "vi") == "kê khai xe"
    prepared = pack.prepare_user_message(
        "kê",
        "vi",
        [AIMessage(content="Xe máy Honda SH Mode, biển số 29E2-986.07.")],
    )
    assert "xe máy (motorbike)" in prepared
    assert "29E2-986.07" in prepared


@pytest.mark.parametrize("artifact", [
    '{@body const [vehicleType,setVehicleType] = DIL.useState("motorbike")}\n<box padding={4}>',
    'DIL.useState("motorbike")\n<radio-group value={vehicleType}>',
    '<box border_radius="xl"><text>Bạn muốn đăng ký loại xe nào?</text></box>',
])
def test_moveinmate_removes_generated_ui_code_after_natural_answer(artifact):
    answer = f"Tôi sẽ hỗ trợ bạn đăng ký phương tiện.\n\n{artifact}"

    assert MoveInMatePack.sanitize_answer(answer, "vi") == "Tôi sẽ hỗ trợ bạn đăng ký phương tiện."


def test_moveinmate_replaces_ui_only_answer_with_safe_instruction():
    answer = MoveInMatePack.sanitize_answer('<radio-group value={vehicleType}>', "vi")

    assert "nút thao tác bên dưới" in answer
    assert "radio-group" not in answer


@pytest.mark.asyncio
async def test_runtime_applies_project_answer_sanitizer():
    class SanitizingPack:
        pack_id = "moveinmate"
        display_name = "Sanitizer test"

        def tools_for(self, actor_id):
            return []

        def system_prompt(self, language):
            return "Reply briefly."

        def sanitize_answer(self, answer, language):
            return answer.split("<box", 1)[0].strip()

    class LeakingModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="Câu trả lời an toàn.\n<box>mã giao diện</box>")

    registry = PackRegistry()
    registry.register(SanitizingPack())
    result = await AgentRuntime(registry, LeakingModel).run(
        "moveinmate", "R001", "sanitize-answer", "Đăng ký phương tiện", "vi",
    )

    assert result.answer == "Câu trả lời an toàn."


def test_explicit_requests_are_routed_without_swapping_ticket_type():
    pack = MoveInMatePack(service=None)
    stay = pack.required_tool_call("Tôi muốn đăng ký tạm trú cho Trần Văn Lan ở cùng căn hộ từ ngày kia. Hãy tạo ticket.")
    complaint = pack.required_tool_call("Tôi muốn phản ánh đèn hành lang tầng 12 bị hỏng. Hãy tạo ticket.")
    visitor = pack.required_tool_call("Đăng ký khách Nguyễn Minh Bình đến chơi ngày mai.")
    assert stay[1]["request_type"] == "temporary_residency"
    assert stay[1]["person_name"] == "Trần Văn Lan"
    assert stay[1]["visit_date"] == "ngày kia"
    assert complaint[1]["request_type"] == "general_support"
    assert visitor[1]["request_type"] == "visitor_registration"
    assert visitor[1]["visitor_name"] == "Nguyễn Minh Bình"
    assert visitor[1]["visit_date"] == "ngày mai"
    assert pack.required_tool_call("Đừng tạo ticket tạm trú cho Trần Văn Lan") is None
    assert pack.required_tool_call("Đăng ký tạm trú cho người ở cùng cần giấy tờ gì?")[0] == "search_regulations"
    assert pack.required_tool_call("Có bao nhiêu người ở căn hộ?")[0] == "get_household_dashboard"
    assert pack.required_tool_call("Có được để giày dép ở hành lang không?")[0] == "search_regulations"
    assert pack.required_tool_call("Giờ yên tĩnh trong nội quy là mấy giờ?")[0] == "search_regulations"


def test_moveinmate_auto_language_follows_foreign_resident():
    prompt = MoveInMatePack(service=None).system_prompt("auto")

    assert "ngôn ngữ trong yêu cầu mới nhất" in prompt
    assert "không dựa vào ngôn ngữ của tài liệu đính kèm" in prompt


@pytest.mark.parametrize("language", ["auto", "vi"])
def test_followup_button_preserves_previous_procedure(language):
    pack = MoveInMatePack(service=None)
    history = [
        HumanMessage(content="Thủ tục đăng ký khách cần những thông tin gì?"),
        AIMessage(content="Bạn cần cung cấp tên khách và ngày đến."),
    ]
    prepared = pack.prepare_user_message("Tôi muốn hỏi thêm về câu trả lời này trong MoveInMate.", language, history)
    assert "Thủ tục đăng ký khách" in prepared
    assert "chưa tạo hoặc gửi" in prepared
    assert pack.required_tool_call(prepared)[0] == "search_regulations"


def test_followup_without_history_opens_procedure_choices():
    pack = MoveInMatePack(service=None)
    prepared = pack.prepare_user_message("Hỏi tiếp", "auto", [])
    assert pack.required_tool_call(prepared) == ("get_onboarding_progress", {})


@pytest.mark.parametrize("message", [
    "Trong MoveInMate có những thủ tục nào?",
    "Tôi mới chuyển đến, cần làm gì?",
    "Hãy kiểm tra tiến độ các thủ tục của tôi.",
])
def test_procedure_lists_read_official_checklist(message):
    assert MoveInMatePack.required_tool_call(message) == ("get_onboarding_progress", {})


@pytest.mark.parametrize("message", [
    "Thủ tục đăng ký khách đến chơi cần những thông tin gì?",
    "Hướng dẫn thủ tục Đăng ký tạm trú trong MoveInMate. Chỉ xem thông tin, chưa tạo hoặc gửi yêu cầu.",
    "Giải thích thủ tục đăng ký internet, không tạo ticket.",
])
def test_viewing_guidance_does_not_route_to_ticket_creation(message):
    assert MoveInMatePack.required_tool_call(message) == ("search_regulations", {"query": message})


@pytest.mark.asyncio
async def test_guidance_turn_cannot_execute_a_model_requested_write(app_container):
    class WriteDuringGuidanceModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage) and messages[-1].name == "search_regulations":
                return AIMessage(content="", tool_calls=[{
                    "name": "create_management_request", "args": {"request_type": "resident_card"}, "id": "forbidden-write",
                }])
            return AIMessage(content="Tôi chỉ đang hướng dẫn, chưa gửi yêu cầu.")

    runtime = AgentRuntime(app_container.packs, WriteDuringGuidanceModel)
    before = app_container.store.list_requests()
    result = await runtime.run(
        "moveinmate", "R001", "read-only-choice",
        "Hướng dẫn thủ tục đăng ký thẻ cư dân. Chỉ xem thông tin, chưa tạo hoặc gửi yêu cầu.", "auto",
    )
    assert app_container.store.list_requests() == before
    assert not result.requires_human
    assert "chưa gửi" in result.answer
    assert not any(trace["tool"] == "create_management_request" for trace in result.tools_used)


@pytest.mark.parametrize("status", ["no_valid_source", "insufficient_site_evidence"])
@pytest.mark.parametrize("language", ["auto", "vi", "en"])
def test_missing_building_source_is_not_an_out_of_scope_refusal(status, language):
    tool = ToolMessage(name="search_regulations", content=json.dumps({"answer_status": status}), tool_call_id="missing-source")
    answer = MoveInMatePack.answer_from_tool(tool, language)
    assert answer
    assert "ngoài phạm vi" not in answer
    assert "verified guidance" in answer if language == "en" else "Yêu cầu khác" in answer


def test_automatic_language_is_preserved_in_missing_source_fallback():
    tool = ToolMessage(name="search_regulations", content='{"answer_status":"no_valid_source"}', tool_call_id="english-missing")
    assert "verified guidance" in MoveInMatePack.answer_from_tool(tool, "auto", "How do I obtain a resident card?")


def test_procedure_menu_summarizes_official_progress_without_duplicate_list():
    tool = ToolMessage(name="get_onboarding_progress", content='{"completed":2,"total":6,"percent":33}', tool_call_id="menu")
    answer = MoveInMatePack.answer_from_tool(tool, "auto", "Tôi có những thủ tục nào?")
    assert "2/6" in answer
    assert "33%" in answer
    assert "Chọn thủ tục" in answer
    assert "mở biểu mẫu hoặc tài liệu" in answer
    assert "1." not in answer


@pytest.mark.asyncio
async def test_missing_source_does_not_let_model_invent_procedure(app_container, monkeypatch):
    monkeypatch.setattr(app_container.tool_service, "search_regulations", lambda *args: {
        "answer_status": "insufficient_site_evidence", "requires_human": True, "sources": [],
    })

    class ForbiddenGuessModel:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            pytest.fail("Missing building sources must not be expanded into invented requirements")

    runtime = AgentRuntime(app_container.packs, ForbiddenGuessModel)
    result = await runtime.run("moveinmate", "R001", "missing-guidance", "Hướng dẫn đăng ký thẻ cư dân", "auto")
    assert "Yêu cầu khác" in result.answer
    assert result.requires_human


@pytest.mark.asyncio
async def test_runtime_accepts_non_moveinmate_project_pack_and_keeps_memory():
    registry = PackRegistry()
    registry.register(DemoPack())
    model = FakeToolCallingModel()
    runtime = AgentRuntime(registry, lambda: model)

    first = await runtime.run("inventory", "user-9", "conversation-1", "Check SKU-1", "en")
    second = await runtime.run("inventory", "user-9", "conversation-1", "Check again", "en")

    assert first.answer == "Item is available."
    assert first.tools_used[0]["tool"] == "lookup_item"
    assert second.answer == "Item is available."
    assert model.max_human_messages == 2


@pytest.mark.asyncio
async def test_successful_ticket_update_cannot_be_described_as_invalid(app_container):
    service = app_container.tool_service
    created = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="001234567890")
    request_id = created["request_id"]
    app_container.store.decide_request(request_id, "needs_info", "Kiểm tra CCCD")
    runtime = AgentRuntime(app_container.packs, MisleadingUpdateModel)
    result = await runtime.run(
        "moveinmate", "R001", "update-valid-cccd",
        f"Bổ sung CCCD 001234567891 cho ticket {request_id}", "vi",
    )
    assert "không hợp lệ" not in result.answer
    assert request_id in result.answer
    assert "Ticket đã gửi" in result.answer
    assert service.get_request_status("R001", request_id)["payload"]["citizen_id"] == "001234567891"
    assert service.list_resident_requests("R001")["total"] == 1


@pytest.mark.asyncio
async def test_runtime_stream_finishes_with_structured_result():
    registry = PackRegistry()
    registry.register(DemoPack())
    runtime = AgentRuntime(registry, FakeToolCallingModel)

    events = [
        event async for event in runtime.stream("inventory", "user-9", "stream-conversation", "Check SKU-1", "en")
    ]

    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "Item is available."
    assert events[-1]["tools_used"][0]["tool"] == "lookup_item"


@pytest.mark.asyncio
async def test_runtime_prevents_cross_actor_conversation_reuse():
    registry = PackRegistry()
    registry.register(DemoPack())
    runtime = AgentRuntime(registry, FakeToolCallingModel)
    await runtime.run("inventory", "user-1", "shared-thread", "Check", "en")
    with pytest.raises(ValueError, match="conversation_scope_mismatch"):
        await runtime.run("inventory", "user-2", "shared-thread", "Check", "en")


@pytest.mark.asyncio
async def test_vehicle_chat_cannot_create_a_ticket_without_the_required_document(app_container):
    runtime = AgentRuntime(app_container.packs, VehicleSlotModel)

    first = await runtime.run("moveinmate", "R001", "vehicle-thread", "Tạo yêu cầu đăng ký xe", "vi")
    second = await runtime.run("moveinmate", "R001", "vehicle-thread", "30K-123.45", "vi")

    assert "đính kèm giấy đăng ký" in first.answer
    assert "Đã tạo REQ-" not in second.answer
    assert app_container.store.list_requests() == []


def test_project_reset_clears_all_actors_without_affecting_other_projects():
    runtime = AgentRuntime(PackRegistry(), FakeToolCallingModel)
    runtime._bindings.update({"resident": ("moveinmate", "R001"), "manager": ("moveinmate", "BQL"),
                              "another": ("moveinmate", "R999"), "keep": ("inventory", "user-1")})
    runtime._graphs.update({name: object() for name in runtime._bindings})
    runtime.reset_project("moveinmate")
    assert set(runtime._bindings) == {"keep"}
    assert set(runtime._graphs) == {"keep"}


@pytest.mark.asyncio
async def test_another_worker_discards_old_conversations_after_reset():
    generation = [0]
    registry = PackRegistry()
    registry.register(DemoPack())
    runtime = AgentRuntime(registry, FakeToolCallingModel, reset_generation=lambda: generation[0])
    await runtime.run("inventory", "user-1", "old-thread", "Check", "en")
    generation[0] += 1
    await runtime.run("inventory", "user-2", "new-thread", "Check", "en")
    assert "old-thread" not in runtime._graphs
    assert "old-thread" not in runtime._bindings


@pytest.mark.asyncio
async def test_reset_during_model_response_prevents_tool_execution():
    generation = [0]

    class ResettingModel(FakeToolCallingModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            generation[0] += 1
            return response

    registry = PackRegistry()
    registry.register(DemoPack())
    runtime = AgentRuntime(registry, ResettingModel, reset_generation=lambda: generation[0])
    with pytest.raises(ValueError, match="session_reset"):
        await runtime.run("inventory", "user-1", "inflight-thread", "Check", "en")
    assert not runtime._graphs
    assert not runtime._bindings

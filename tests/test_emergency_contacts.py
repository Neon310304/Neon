import pytest

from src.project_packs.moveinmate import MoveInMatePack


@pytest.mark.parametrize("language,prompt", [("vi", "Số liên hệ khẩn cấp là gì?"), ("vi", "Số liên lạc khẩn cấp là gì?"), ("en", "emergency contact numbers")])
def test_national_emergency_numbers_have_clear_roles_and_no_invented_building_contact(language, prompt):
    answer = MoveInMatePack.answer_from_request(prompt, language)
    for number in ("112", "113", "114", "115"):
        assert number in answer
    assert "BQL" in answer if language == "vi" else "property management" in answer
    assert "chưa có" in answer if language == "vi" else "do not have a verified" in answer


@pytest.mark.parametrize("language,prompt", [("vi", "Tôi muốn nhảy từ ban công"), ("en", "I want to kill myself")])
def test_imminent_harm_prioritizes_medical_emergency_without_mixing_emergency_numbers(language, prompt):
    answer = MoveInMatePack.answer_from_request(prompt, language)
    assert "115" in answer
    assert "112" not in answer
    assert "cấp cứu y tế" in answer if language == "vi" else "medical emergency" in answer
    assert "ở cùng" in answer if language == "vi" else "stay with you" in answer


def test_other_requests_keep_normal_agent_routing():
    assert MoveInMatePack.answer_from_request("Ticket REQ-123 đã được duyệt chưa?", "vi") is None
    assert MoveInMatePack.answer_from_request("Làm thẻ cư dân thế nào?", "vi") is None
    assert MoveInMatePack.answer_from_request("Tôi cần hỗ trợ: Xác nhận liên hệ khẩn cấp.", "vi") is None
    assert MoveInMatePack.answer_from_request("Cập nhật người liên hệ khẩn cấp của tôi", "vi") is None

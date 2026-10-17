"""Small, deterministic guardrails for the project agent.

The model is still responsible for normal intent handling, but these checks run
before the model so prompt injection, abusive automation and clearly unrelated
requests cannot consume tools or leak the system prompt.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str = "allowed"
    message: str = ""


def message_text(message: str | list[dict[str, Any]]) -> str:
    if isinstance(message, str):
        return message.strip()
    parts: list[str] = []
    for item in message:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("text")
                )
    return " ".join(parts).strip()


def request_text(message: str | list[dict[str, Any]]) -> str:
    if isinstance(message, str):
        return message.strip()
    for item in message:
        if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
            text = item.get("text")
            if isinstance(text, str):
                return text.strip()
    return message_text(message)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    value = unicodedata.normalize("NFD", value.casefold())
    value = value.replace("đ", "d")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^\w]+|_+", " ", value).strip()


_INJECTION_PATTERNS = (
    r"ignore (all|any|the|previous|prior) (instructions|messages|rules)",
    r"disregard (all|any|the|previous|prior)",
    r"reveal (your|the) (system|developer|hidden) prompt",
    r"show (me )?(your|the) (system|developer|hidden) prompt",
    r"print (your|the) (system|developer) message",
    r"bypass (your|the) (safety|security|policy)",
    r"jailbreak",
    r"bo qua (?:(?:tat ca|cac|moi|nhung) )?huong dan",
    r"bo qua (?:(?:tat ca|cac|moi|nhung) )?quy tac",
    r"tiet lo (system|developer|hidden|an) prompt",
    r"hien thi (system|developer|hidden|an) prompt",
    r"(xem|cho xem).{0,20}(system|developer|hidden|an) prompt",
    r"\b(system|developer|hidden) prompt\b",
    r"be khoa (bao mat|he thong|tro ly)",
    r"bo qua (luat|bao mat|kiem tra)",
    r"(reveal|show|print|dump|leak).{0,30}(prompt|instructions|rules|token|secret)",
    r"(developer|system) mode",
    r"act as (an? )?(unrestricted|different|developer|system)",
    r"pretend (to be|you are)",
    r"\b(?:gia lam|gia vo la|mao danh|dong vai|nhap vai)\s+(?:mot\s+)?(?:admin|quan tri vien|ban quan ly|bql)\b",
    r"(?:bo qua|quen|khong tuan theo) (?:tat ca |cac |moi |nhung |nhung chi dan |)?(?:chi dan|chi thi|quy dinh|(?:rang|giang) buoc|gioi han|huong dan|quy tac) (?:cua )?(?:truoc|cu|he thong|noi bo|an toan)",
    r"(?:dong vai|nhap vai|gia vo la|hay la) (?:mot )?(?:tro ly|ai|chatbot) (?:khong gioi han|tu do|khong kiem duyet)",
    r"(?:system|developer|assistant)\s+(?:override|instruction|instructions|message)\b",
    r"(?:decode|giai ma).{0,40}(?:base64|rot13|hex).{0,40}(?:execute|follow|thuc hien|lam theo)",
    r"\b(?:show|print|dump|reveal|read|leak|cho xem|xem|doc|in ra|tiet lo|hien thi|lay)\b.{0,80}\b(?:api key|api token|bien moi truong|environment variables?|env file|file env|settings encryption key|database url|jwt secret|private key|khoa ma hoa)\b",
    r"\b(?:bo qua|khong can|skip|bypass).{0,20}\b(?:xac nhan|confirmation)\b",
)
_ABUSE_PATTERNS = (
    r"\bddos\b",
    r"ransomware",
    r"steal (password|credential|token)",
    r"credential stuffing",
    r"malware",
    r"phishing kit",
    r"scrape (all|every) (resident|user|ticket)",
    r"export (all|every) (resident|user|ticket|data)",
    r"spam (all|every)",
    r"spam (tat ca|toan bo)",
    r"danh cap (mat khau|thong tin dang nhap|token)",
    r"thu thap (toan bo|tat ca) (cu dan|nguoi dung|ticket)",
    r"xuat (toan bo|tat ca) (du lieu|ho so)",
    r"quet (toan bo|tat ca) (cu dan|nguoi dung)",
    r"xoa (toan bo|tat ca|het) (du lieu|ho so|cu dan|nguoi dung|ticket)",
    r"(bypass|circumvent|disable) (auth|authentication|access control|security)",
    r"(delete|erase|destroy|wipe) (all|every|the) (resident|user|ticket|data|record)",
    r"(exfiltrate|dump|leak) (resident|user|private|personal|internal) data",
    r"(hack|exploit|attack) (the )?(system|api|portal|database)",
)
_OUT_OF_SCOPE_TERMS = (
    "viet code",
    "write code",
    "python script",
    "javascript code",
    "debug code",
    "giai bai tap",
    "solve this equation",
    "thoi tiet",
    "tin tuc",
    "bong da",
    "chinh tri",
    "weather",
    "news",
    "recipe",
    "tell me a joke",
    "tell me a story",
)
_CREATIVE_OUTPUT = (
    r"(?:bai hat|ca khuc|loi bai hat|loi nhac|ban nhac|tac pham am nhac|nhac rap|bai rap|rap|"
    r"bai tho|doan tho|tho|bai van|truyen|cau chuyen|truyen ngan|tieu thuyet|truyen cuoi|"
    r"song|lyrics|music|poem|poetry|essay|story|stories|joke|novel|rap song|screenplay)"
)
_REQUEST_QUALIFIERS = (
    r"(?:\s+(?:giup|ho|cho|toi|minh|ban|mot|1|vai|nhieu|mau|ban|doan|ngan|moi|vui|"
    r"cu|dan|can|ho|quan|ly|moveinmate|a|an|the|me|us|some|new|short|funny|beautiful|original|little|welcome|creative|sample)){0,12}"
)
_OUT_OF_SCOPE_TASK_PATTERNS = (
    rf"\b(?:viet|sang tac|tao|lam|soan|ke|hat|rap|cho|write|compose|create|generate|sing|tell|make|give|produce){_REQUEST_QUALIFIERS}\s+{_CREATIVE_OUTPUT}\b",
    rf"\b(?:chuyen|bien|dien dat|trinh bay|viet lai|tom tat|convert|turn|rewrite|summarize|translate).{{0,90}}\b(?:thanh|duoi dang|dang|bang|as|into|to|in the form of){_REQUEST_QUALIFIERS}\s+{_CREATIVE_OUTPUT}\b",
    rf"^(?:{_CREATIVE_OUTPUT})\s+(?:ve|chao mung|lien quan|cho|about|for|on)\b",
    rf"\b(?:i want|i need|can i have|toi muon|toi can|cho toi){_REQUEST_QUALIFIERS}\s+{_CREATIVE_OUTPUT}\b",
    r"\b(?:viet|tao|lap trinh|write|generate|create|debug)\s+(?:giup |cho toi |mot |a |some |the )?(?:code|ma(?: nguon)?|python script|javascript code|program|script)\b",
    r"\b(?:giai|lam)\s+(?:giup |cho toi )?(?:bai tap|de thi|phuong trinh|bai toan)\b",
    r"\b(?:solve|do|complete)\s+(?:my |this |the |a )?(?:homework|equation|math problem|exam)\b",
    r"\b(?:hoc thuoc(?: long)?|on thi|on tap|memorize|memorise)\b.{0,80}\b(?:bai hoc|bai giang|bai thi|de thi|mon hoc|toan hoc|vat ly|hoa hoc|lesson|exam|homework)\b",
)
_STUDY_TASK_PATTERNS = (
    r"\b(?:hoc thuoc(?: long)?|on thi|on tap|memorize|memorise|memorizing|memorising)\b",
)
_RESIDENT_REFERENCE_PATTERN = (
    r"\b(?:noi quy|quy dinh|thu tuc|huong dan an toan|so do thoat hiem|thong bao bql|"
    r"building rules|resident procedures|evacuation plan)\b"
)
_PROJECT_TERMS = (
    "moveinmate",
    "cu dan",
    "can ho",
    "khu dan cu",
    "ban quan ly",
    "ticket",
    "thu tuc",
    "noi quy",
    "the cu dan",
    "tam tru",
    "chuyen vao",
    "internet",
    "gui xe",
    "khach",
    "ho so",
    "tai lieu",
    "ho tro",
    "yeu cau",
    "dang ky",
    "cu tru",
    "viec",
    "can lam",
    "tien do",
    "trang thai",
    "hoan tat",
    "checklist",
    "tep dinh kem",
    "dinh kem",
    "attachment",
    "document",
    "phan tich ho so",
    # English labels are useful for residents who use the bilingual UI.
    "moveinmate",
    "resident",
    "residence",
    "apartment",
    "building",
    "property management",
    "management office",
    "move in",
    "onboarding",
    "visitor",
    "guest",
    "parking",
    "vehicle",
    "regulation",
    "request",
    "ticket",
    "temporary residence",
    "maintenance",
    "complaint",
    "delivery",
    "pet",
    "pool",
    "gym",
    "household",
    "progress",
    "status",
    "checklist",
    "what else",
    "what do i need",
)
_SMALL_TALK_PATTERNS = (
    r"^(hi|hello|hey|xin chao|chao|alo|good morning|good afternoon|good evening)$",
    r"^(cam on( ban)?|thanks( you)?|thank you|thankyou|ok|okay|oke|yes|no|co|khong|duoc|duoc roi)$",
)
_MOVEINMATE_ONLY_MESSAGE = (
    "Tôi chỉ trả lời trong phạm vi MoveInMate: hồ sơ cư dân, thủ tục chuyển vào, "
    "nội quy khu, yêu cầu và ticket gửi Ban quản lý."
)
_MOVEINMATE_ONLY_MESSAGE_EN = (
    "I only answer questions within MoveInMate: resident records, move-in procedures, "
    "building rules, resident services and management tickets."
)
_INJECTION_BLOCKED_EN = (
    "I can't change or disclose internal instructions. I can help with MoveInMate "
    "procedures, rules and resident requests."
)
_ABUSE_BLOCKED_EN = (
    "I can't help with harmful activity, unauthorized data collection or system abuse. "
    "You can ask about resident services in MoveInMate."
)
_NON_OPERATIONAL_MESSAGE = (
    "MoveInMate không sáng tác bài hát, thơ, truyện, viết mã hoặc làm bài tập, "
    "kể cả khi nội dung có nhắc đến cư dân hay căn hộ. "
    "Bạn có thể chọn thủ tục để xem hướng dẫn, tra cứu nội quy hoặc theo dõi ticket."
)
_NON_OPERATIONAL_MESSAGE_EN = (
    "MoveInMate does not create songs, poems, stories, code or homework, even about residents or apartments. "
    "You can choose a procedure, check building rules or track a management ticket."
)


def _requests_non_operational_output(folded: str, patterns: tuple[str, ...] = _OUT_OF_SCOPE_TASK_PATTERNS) -> bool:
    for pattern in patterns:
        for match in re.finditer(pattern, folded):
            prefix = folded[max(0, match.start() - 40):match.start()]
            if re.search(r"(?:khong|dung|khong can|khong duoc|do not|dont|don t|never)\s*$", prefix):
                continue
            return True
    return False


def evaluate_request(
    project_id: str,
    message: str | list[dict[str, Any]],
    language: str = "vi",
) -> PolicyDecision:
    """Return a safe decision before any model/tool call is made.

    The reusable runtime can serve an internal registered pack. The public API
    enforces the MoveInMate-only boundary before calling that runtime.
    """
    if project_id != "moveinmate":
        return PolicyDecision(True)

    text = message_text(message)
    full_text = _fold(text)
    folded = _fold(request_text(message))
    english = language == "en" or (
        language == "auto"
        and bool(re.search(r"\b(what|how|where|when|why|please|tell|weather|news|code|write)\b", folded))
        and not bool(re.search(r"\b(toi|ban|muon|cho|cua|trong|khong|duoc|dang|ky)\b", folded))
    )
    scope_message = _MOVEINMATE_ONLY_MESSAGE_EN if english else _MOVEINMATE_ONLY_MESSAGE
    if not folded:
        return PolicyDecision(
            False,
            "empty_message",
            "Describe what you need help with in MoveInMate."
            if english
            else "Bạn hãy mô tả việc cần hỗ trợ trong MoveInMate.",
        )

    if any(re.search(pattern, full_text) for pattern in _INJECTION_PATTERNS):
        return PolicyDecision(
            False,
            "prompt_injection_blocked",
            _INJECTION_BLOCKED_EN if english else "Tôi không thể thay đổi hoặc tiết lộ hướng dẫn nội bộ. Tôi chỉ hỗ trợ các thủ tục, nội quy và yêu cầu của MoveInMate.",
        )
    if any(re.search(pattern, full_text) for pattern in _ABUSE_PATTERNS):
        return PolicyDecision(
            False,
            "abuse_blocked",
            _ABUSE_BLOCKED_EN if english else "Tôi không thể hỗ trợ hành vi gây hại, thu thập dữ liệu trái phép hoặc lạm dụng hệ thống. Bạn có thể hỏi về dịch vụ cư dân trong MoveInMate.",
        )
    if _requests_non_operational_output(folded):
        return PolicyDecision(False, "out_of_scope", _NON_OPERATIONAL_MESSAGE_EN if english else _NON_OPERATIONAL_MESSAGE)

    # Remembering actual resident instructions can be relevant. General study
    # requests must not reach a procedure shortcut just because they say "for me".
    if _requests_non_operational_output(folded, _STUDY_TASK_PATTERNS) and not re.search(_RESIDENT_REFERENCE_PATTERN, folded):
        return PolicyDecision(False, "out_of_scope", scope_message)

    # A request to explain a building news bulletin or the weather on moving
    # day can contain these words. Let the model resolve mixed/ambiguous intent
    # within the project scope instead of rejecting it by substring alone.
    if any(term in folded for term in _OUT_OF_SCOPE_TERMS) and not any(
        term in folded for term in _PROJECT_TERMS
    ):
        return PolicyDecision(False, "out_of_scope", scope_message)

    # Names, identifiers, dates and ordinary follow-up replies need not repeat
    # project keywords. The scoped model prompt resolves them against history
    # or asks a clarifying question; API authorization still governs every tool.
    if any(re.fullmatch(pattern, folded) for pattern in _SMALL_TALK_PATTERNS):
        return PolicyDecision(True)
    return PolicyDecision(True)

"""Fixed A2UI v0.9.1 catalog messages for MoveInMate answers.

The catalog is deliberately small and server-owned. The model never emits
HTML/JavaScript or arbitrary component names; the browser only renders the
components listed here, with allow-listed navigation or chat actions.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from src.repositories.store import COMING_SOON_TASK_CODES, checklist_sort_key
from src.services.procedure_knowledge import GUIDES, contains_phrase, guide_codes, wants_guidance
from src.tools.agent_tools import PROCEDURES, normalize

A2UI_VERSION = "v0.9.1"
A2UI_CATALOG_ID = "https://a2ui.org/specification/v0_9_1/catalogs/basic/catalog.json"
FORM_LABELS = {
    "profile": "Nhập thông tin cư dân",
    "verify_residency": "Xem thông tin cư trú",
    "temporary_residency": "Thêm người cư trú",
    "vehicle_registration": "Đăng ký thẻ xe",
    "resident_card": "Đăng ký thẻ cư dân",
    "move_in_schedule": "Chọn lịch chuyển đồ",
    "internet_registration": "Đăng ký internet",
    "visitor_registration": "Đăng ký khách",
    "card_replacement": "Báo mất / cấp lại thẻ",
    "general_support": "Yêu cầu khác",
    "account_registration": "Đăng ký tài khoản cư dân phụ thuộc",
}


def form_for_question(question: str) -> str | None:
    text = normalize(question)
    codes = guide_codes(question)
    if len(codes) == 1 and GUIDES[codes[0]].get("form") in FORM_LABELS:
        return GUIDES[codes[0]]["form"]
    if any(contains_phrase(text, phrase) for phrase in ("tam tru", "them nguoi cu tru", "nguoi o cung", "temporary residency", "temporary residence", "temporary_residency")):
        return "temporary_residency"
    if any(contains_phrase(text, phrase) for phrase in ("thong tin cu tru", "ho so cu tru", "xac nhan cu tru", "verify residency", "verify_residency")):
        return "verify_residency"
    if any(contains_phrase(text, phrase) for phrase in ("the xe", "thong tin xe", "phuong tien", "bien so", "dang ky xe", "vehicle", "parking", "o to", "xe may", "motorbike")):
        return "vehicle_registration"
    if any(contains_phrase(text, phrase) for phrase in ("the cu dan", "resident card", "resident_card")):
        return "resident_card"
    if any(contains_phrase(text, phrase) for phrase in ("thong tin cu dan", "ho so cu dan", "resident profile", "them thong tin")):
        return "profile"
    return None


def build_answer_surface(
    answer: str,
    *,
    conversation_id: str,
    requires_human: bool = False,
    question: str = "",
    procedures: list[dict[str, Any]] | None = None,
    include_actions: bool = True,
) -> dict[str, Any]:
    surface_id = f"answer_{conversation_id}_{uuid4().hex[:8]}"
    topic = question.strip()[:700]
    status_labels = {
        "pending": "Chưa thực hiện",
        "in_progress": "Đang thực hiện",
        "submitted": "Đã gửi, chờ Ban quản lý",
        "in_review": "Ban quản lý đang xem xét",
        "needs_info": "Cần bổ sung thông tin",
        "approved": "Đã duyệt, chưa hoàn tất",
        "completed": "Đã hoàn tất",
        "rejected": "Bị từ chối",
        "cancelled": "Đã hủy",
    }
    choices = []
    seen = set()
    related_codes = guide_codes(question)
    selected = related_codes if wants_guidance(question) else []
    if selected:
        existing = {item["code"]: item for item in procedures or []}
        procedures = [existing.get(code) or {
            "code": code, "reason": GUIDES[code]["steps"][0], "suggestion": True,
        } for code in selected if code in PROCEDURES]
    topic_text = normalize(question)

    def procedure_priority(task):
        code = task.get("code")
        positions = []
        for alias in GUIDES.get(code, {}).get("aliases", ()):
            match = re.search(r"(?<!\w)" + r"[\W_]+".join(re.escape(word) for word in alias.split()) + r"(?!\w)", topic_text)
            if match:
                positions.append(match.start())
        return (code not in related_codes, min(positions, default=len(topic_text)), checklist_sort_key(task))

    for task in sorted(procedures or [], key=procedure_priority):
        code = task.get("code")
        if code not in PROCEDURES or code in seen:
            continue
        seen.add(code)
        coming_soon = bool(task.get("coming_soon", code in COMING_SOON_TASK_CODES))
        choices.append({
            "code": code,
            "title": "Xác thực quyền sở hữu căn hộ" if code == "verify_residency" and task.get("title") == "Xác thực quyền sở hữu căn hộ" else PROCEDURES[code][0],
            "description": str(task.get("reason", "")),
            "status": "Coming soon" if coming_soon else "Đã hoàn tất · Chưa có nhu cầu" if task.get("completion_reason") == "not_needed" else "Xem hướng dẫn" if task.get("suggestion") else status_labels.get(task.get("status"), "Chưa thực hiện"),
            "status_code": "suggested" if task.get("suggestion") else task.get("status", "pending"),
            "requirement": "Gợi ý theo câu hỏi" if task.get("suggestion") else "Bắt buộc" if task.get("required", PROCEDURES[code][1]) else "Tùy chọn",
            "coming_soon": coming_soon,
        })
    components = [
        {"id": "root", "component": "Card", "child": "body"},
        {"id": "body", "component": "Column", "children": ["actions"] if include_actions else []},
        {"id": "actions", "component": "Row", "variant": "actions", "children": ["support", "progress"]},
        {
            "id": "browse", "component": "Button", "text": "Chọn thủ tục", "variant": "primary",
            "action": {"event": {"name": "open_procedures", "context": {}}},
        },
        {
            "id": "progress",
            "component": "Button",
            "text": "Xem việc cần làm",
            "variant": "secondary",
            "action": {"event": {"name": "open_tab", "context": {"tab": "progress"}}},
        },
        {
            "id": "sources", "component": "Button", "text": "Đọc tài liệu", "variant": "secondary",
            "action": {"event": {"name": "open_tab", "context": {"tab": "regulations"}}},
        },
        {
            "id": "support", "component": "Button", "text": "Yêu cầu khác", "variant": "secondary",
            "action": {"event": {"name": "open_form", "context": {"form": "general_support"}}},
        },
    ]
    related = []
    if not include_actions:
        components = components[:2]
    elif topic:
        form = form_for_question(topic)
        ticket_id = re.search(r"\bREQ-[A-Z0-9]{8}\b", topic.upper())
        tracking = ticket_id or "requests" in guide_codes(topic)
        if tracking:
            components.append({
                "id": "entry", "component": "Button", "text": f"Xem {ticket_id.group(0)}" if ticket_id else "Xem ticket đã gửi", "variant": "primary",
                "action": {"event": {"name": "open_tab", "context": {"tab": "tickets", **({"request_id": ticket_id.group(0)} if ticket_id else {})}}},
            })
            related.append("entry")
        elif form and form != "general_support":
            components.append({
                "id": "entry", "component": "Button", "text": FORM_LABELS[form], "variant": "primary",
                "action": {"event": {"name": "open_form", "context": {"form": form}}},
            })
            related.append("entry")
        elif len(guide_codes(topic)) == 1 and GUIDES[guide_codes(topic)[0]].get("tab") in {"regulations", "tickets", "notifications", "household"}:
            guide = GUIDES[guide_codes(topic)[0]]
            components.append({
                "id": "entry", "component": "Button", "text": "Xem nội quy" if guide["tab"] == "regulations" else guide["title"], "variant": "primary",
                "action": {"event": {"name": "open_tab", "context": {"tab": guide["tab"]}}},
            })
            related.append("entry")
        elif not form:
            related.append("browse")
    if choices and include_actions:
        targets = {str(component["action"]["event"]) for component in components if component["id"] in [*related, "support", "progress"]}
        for choice in choices:
            code = choice["code"]
            if code in FORM_LABELS:
                event = {"name": "open_form", "context": {"form": code}}
                label = FORM_LABELS[code]
            elif code in {"confirm_regulations", "safety_briefing"}:
                event = {"name": "open_tab", "context": {"tab": "regulations"}}
                label = "Xem nội quy"
            else:
                continue
            if str(event) in targets:
                continue
            targets.add(str(event))
            button_id = f"related_{code}"
            related.append(button_id)
            components.append({"id": button_id, "component": "Button", "text": label, "variant": "secondary",
                               "disabled": choice["coming_soon"] and event["name"] != "open_tab", "action": {"event": event}})
    elif choices:
        components.append({"id": "list_title", "component": "Text", "variant": "title", "text": "Chọn thủ tục bạn muốn xem"})
        rows = []
        for index, choice in enumerate(choices):
            row_id = f"procedure_{choice['code']}"
            rows.append(row_id)
            if choice["code"] in FORM_LABELS:
                event = {"name": "open_form", "context": {"form": choice["code"]}}
                label = FORM_LABELS[choice["code"]]
            elif choice["code"] in {"confirm_regulations", "safety_briefing"}:
                event = {"name": "open_tab", "context": {"tab": "regulations"}}
                label = "Đọc tài liệu"
            else:
                event = {"name": "focus_input", "context": {"message": f"Tôi cần hỗ trợ: {choice['title']}."}}
                label = "Trao đổi với agent"
            components.extend([
                {"id": row_id, "component": "Row", "variant": "procedure", "taskCode": choice["code"], "children": [f"{row_id}_copy", f"{row_id}_select"]},
                {"id": f"{row_id}_copy", "component": "Column", "variant": "procedure-copy", "children": [f"{row_id}_title", f"{row_id}_requirement", f"{row_id}_status", f"{row_id}_reason"]},
                {"id": f"{row_id}_title", "component": "Text", "variant": "title", "text": {"path": f"/procedures/{index}/title"}},
                {"id": f"{row_id}_requirement", "component": "Text", "variant": "requirement", "text": {"path": f"/procedures/{index}/requirement"}},
                {"id": f"{row_id}_status", "component": "Text", "variant": "status", "text": {"path": f"/procedures/{index}/status"}},
                {"id": f"{row_id}_reason", "component": "Text", "variant": "muted", "text": {"path": f"/procedures/{index}/description"}},
                {
                    "id": f"{row_id}_select", "component": "Button", "text": label, "variant": "primary",
                    "ariaLabel": f"{label}: {choice['title']}",
                    "action": {"event": event},
                },
            ])
            if choice["coming_soon"] and event["name"] != "open_tab":
                components[-1]["disabled"] = True
        components.append({"id": "divider", "component": "Divider"})
        components[1]["children"] = rows
    if include_actions and related:
        if len(related) > 1 and "browse" in related:
            related.remove("browse")
        components.append({"id": "related", "component": "Row", "variant": "actions", "children": related})
        components[1]["children"] = ["related", "actions"]
    return {
        "version": A2UI_VERSION,
        "surface_id": surface_id,
        "messages": [
            {
                "version": A2UI_VERSION,
                "createSurface": {
                    "surfaceId": surface_id,
                    "catalogId": A2UI_CATALOG_ID,
                    "sendDataModel": False,
                },
            },
            {
                "version": A2UI_VERSION,
                "updateComponents": {"surfaceId": surface_id, "components": components},
            },
            {
                "version": A2UI_VERSION,
                "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": {"answer": answer, "procedures": choices}},
            },
        ],
    }

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.tools import tool

from src.rag import RegulationRetriever
from src.rag.retriever import SEARCH_STOPWORDS, TOKEN_RE, TOPIC_ANCHORS
from src.repositories import Store
from src.services.procedure_knowledge import (
    application_guidance,
    contains_phrase,
    fold_text,
    guide_codes,
    is_legal_question,
    legal_category,
)

TOOL_NAMES = [
    "get_resident_profile",
    "search_regulations",
    "build_checklist",
    "get_onboarding_progress",
    "update_task_status",
    "create_management_request",
    "list_resident_requests",
    "get_request_status",
    "cancel_management_request",
    "update_management_request",
    "get_household_dashboard",
]

REQUEST_TYPES = {
    "verify_residency",
    "temporary_residency",
    "move_in_schedule",
    "handover_inspection",
    "resident_card",
    "emergency_contact",
    "account_registration",
    "vehicle_registration",
    "internet_registration",
    "card_replacement",
    "visitor_registration",
    "general_support",
}
REQUEST_STATUSES = {
    "submitted",
    "in_review",
    "needs_info",
    "approved",
    "rejected",
    "completed",
    "cancelled_by_resident",
}
ACTIVE_REQUEST_STATUSES = {"submitted", "in_review", "needs_info", "approved"}
REQUEST_STATUS_GROUPS = {"active", "finished"}

PROCEDURES = {
    "verify_residency": ("Xác nhận thông tin cư trú", True),
    "temporary_residency": ("Đăng ký tạm trú", True),
    "move_in_schedule": ("Đăng ký lịch chuyển đồ", True),
    "handover_inspection": ("Kiểm tra bàn giao và chỉ số điện nước", False),
    "resident_card": ("Đăng ký thẻ cư dân", True),
    "emergency_contact": ("Xác nhận liên hệ khẩn cấp", False),
    "account_registration": ("Đăng ký tài khoản cư dân phụ thuộc", False),
    "confirm_regulations": ("Đọc và xác nhận nội quy", True),
    "safety_briefing": ("Đọc hướng dẫn an toàn và thoát hiểm", False),
    "vehicle_registration": ("Đăng ký phương tiện", True),
    "internet_registration": ("Đăng ký internet", False),
    "visitor_registration": ("Đăng ký khách đến chơi", False),
    "card_replacement": ("Báo mất / cấp lại thẻ", False),
    "general_support": ("Phản ánh / yêu cầu khác", False),
}


def normalize(text: str) -> str:
    return fold_text(text)


def normalize_person_name(value: str | None, *, conversational: bool = False) -> str:
    tokens = unicodedata.normalize("NFC", value or "").split()
    if conversational and len(tokens) > 2 and tokens[0].casefold() in {"anh", "chị", "chi", "ông", "ong", "bà", "ba", "cô", "co", "chú", "chu", "bác", "bac", "em"}:
        tokens = tokens[1:]
    return " ".join(token.capitalize() for token in tokens)


def normalize_vehicle_type(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize(value)
    if any(contains_phrase(text, phrase) for phrase in ("motorbike", "motorcycle", "xe may")):
        return "motorbike"
    if any(contains_phrase(text, phrase) for phrase in ("car", "automobile", "o to")):
        return "car"
    return None


def normalize_request_type(value: str | None) -> str | None:
    if not value:
        return None
    if value in REQUEST_TYPES:
        return value
    text = normalize(value)
    if text in {"all", "tat ca", "any"}:
        return None
    if any(phrase in text for phrase in ("cap tai khoan", "dang ky tai khoan", "account registration")):
        return "account_registration"
    if any(phrase in text for phrase in ("tam tru", "o cung", "nguoi cung o", "qua dem", "luu tru", "temporary residence")):
        return "temporary_residency"
    if "khach" in text or "visitor" in text or "nguoi nha" in text:
        return "visitor_registration"
    if any(phrase in text for phrase in ("phan anh", "kien nghi", "khieu nai", "yeu cau khac", "support", "complaint")):
        return "general_support"
    if "chuyen do" in text or "thang hang" in text:
        return "move_in_schedule"
    if "ban giao" in text or "chi so dien nuoc" in text:
        return "handover_inspection"
    if "lien he khan cap" in text:
        return "emergency_contact"
    if "internet" in text or "mang" in text:
        return "internet_registration"
    if "xac nhan cu tru" in text:
        return "verify_residency"
    if "xe" in text or "vehicle" in text or "car" in text or "motorbike" in text:
        return "vehicle_registration"
    if "mat" in text or "cap lai" in text or "replacement" in text or "lost" in text:
        return "card_replacement"
    if "the" in text or "resident card" in text:
        return "resident_card"
    return value


def normalize_request_status(value: str | None) -> str | None:
    if not value:
        return None
    if value in REQUEST_STATUSES | REQUEST_STATUS_GROUPS:
        return value
    text = normalize(value)
    if text in {"all", "tat ca", "any"}:
        return None
    aliases = {
        "active": ("dang mo", "chua xong", "chua hoan tat", "active", "open"),
        "finished": ("da ket thuc", "ket thuc", "finished", "closed"),
        "submitted": ("da gui", "cho xu ly", "dang cho", "moi gui", "chua xu ly", "pending"),
        "in_review": ("dang xem", "dang xu ly", "review"),
        "needs_info": ("can bo sung", "thieu thong tin", "need info"),
        "approved": ("da duyet", "duoc duyet", "approve"),
        "rejected": ("tu choi", "bi tu choi", "reject"),
        "completed": ("hoan tat", "da xong", "xu ly xong", "complete"),
        "cancelled_by_resident": ("da huy", "huy boi nguoi dung", "cancelled"),
    }
    for status, phrases in aliases.items():
        if any(phrase in text for phrase in phrases):
            return status
    return value


def normalize_visit_date(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    original = value.strip()
    folded = normalize(original)
    today = datetime.now(timezone(timedelta(hours=7))).date()
    if folded in {"hom nay", "today"}:
        parsed = today
    elif folded in {"mai", "ngay mai", "tomorrow"}:
        parsed = today + timedelta(days=1)
    elif folded in {"ngay kia", "ngay mot", "mot", "the day after tomorrow"}:
        parsed = today + timedelta(days=2)
    else:
        parsed = None
        for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(original, date_format).date()
                break
            except ValueError:
                continue
        if parsed is None:
            short = re.fullmatch(r"(?:ngay\s+|hom\s+)?(\d{1,2})(?:\s*/\s*|\s*-\s*|\s+thang\s+)(\d{1,2})(?:(?:\s*/\s*|\s*-\s*|\s+nam\s+)(\d{4}))?", folded)
            if short:
                try:
                    parsed = today.replace(year=int(short[3]) if short[3] else today.year, month=int(short[2]), day=int(short[1]))
                except ValueError:
                    return None, "invalid_date_format"
        if parsed is None:
            return None, "invalid_date_format"
    if parsed < today:
        return None, "visit_date_in_past"
    return parsed.isoformat(), None


def extract_schedule_fields(message: str) -> dict[str, str]:
    source = re.sub(r"\b(lúc|luc|vào|vao|ngày|ngay|hôm|hom)(?=\d)", r"\1 ", unicodedata.normalize("NFC", message.casefold()))
    text = normalize(source)
    result = {}
    dates = []
    date_pattern = (
        r"(?<![\w/:-])(?:ngay\s+(?:mai|kia|mot)|hom\s+nay|today|tomorrow|"
        r"(?:sang|trua|chieu|toi|dem)\s+nay|mai|"
        r"\d+(?:\s*[/-]\s*\d+){1,2}|"
        r"\d{1,2}\s+thang\s+\d{1,2}(?:\s+nam\s+\d{2,4})?)(?![\w/:-])"
    )
    for match in re.finditer(date_pattern, text):
        if match.group() == "mai" and re.search(r"\b(?:ten|khach|ho ten)\b.{0,40}$", text[:match.start()]):
            continue
        if re.search(r"\b(?:khong|chua|dung|ko|k)(?:\s+(?:phai|chon|vao|muon|can|lay|nhan|hen|ngay)){0,4}\s*$", text[:match.start()]):
            result["date_error"] = "unconfirmed_date"
            continue
        date_text = "hôm nay" if re.fullmatch(r"(?:sang|trua|chieu|toi|dem)\s+nay", match.group()) else match.group()
        value, error = normalize_visit_date(date_text)
        if error:
            result["date_error"] = error
        elif value:
            dates.append(value)
    if len(set(dates)) > 1:
        result["date_error"] = "ambiguous_date"
    elif dates and "date_error" not in result:
        result["date"] = dates[0]
    period = r"(?:buoi\s+)?(?:sang|trua|chieu|toi|dem)|am|pm"
    pattern = (
        r"(?<![\w:])(?P<hour>\d{1,2})"
        r"(?::(?P<minute>\d{1,2})(?!\d)|\s*(?:h|gio)(?![a-z])"
        r"(?:\s*(?P<hminute>\d{1,2})(?![\d/-])(?:\s*phut)?)?|(?=\s*(?:am|pm)\b|\s+(?:" + period + r")))"
        r"(?:\s*(?P<half>ruoi))?(?:\s*(?P<period>" + period + r"))?(?!\w)"
    )
    times = []
    for match in re.finditer(pattern, text):
        if re.search(r"\b(?:khong|chua|dung|ko|k)(?:\s+(?:phai|chon|vao|luc|muon|can|lay|nhan|hen|gio)){0,4}\s*$", text[:match.start()]):
            result["time_error"] = "unconfirmed_time"
            continue
        hour = int(match["hour"])
        minute = int(match["minute"] or match["hminute"] or ("30" if match["half"] else "0"))
        preceding = re.search(r"\b(" + period + r")(?:\s+(?:ngay|mai|kia|mot|hom|nay|luc|vao))*\s*$", text[:match.start()])
        if preceding and source[preceding.start(1):preceding.end(1)] == "tôi":
            preceding = None
        if preceding and preceding.group(1) == "sang" and source[preceding.start(1):preceding.end(1)] != "sáng" and re.search(r"\b(?:doi|chuyen|sua|thay)(?:\s+\w+){0,4}\s*$", text[:preceding.start()]):
            preceding = None
        qualifier = (match["period"] or (preceding.group(1) if preceding else "")).removeprefix("buoi ")
        if match["period"] and source[match.start("period"):match.end("period")] == "tôi":
            qualifier = ""
        if qualifier in {"chieu", "toi", "pm"} and 1 <= hour < 12:
            hour += 12
        elif qualifier in {"sang", "am"} and hour == 12:
            hour = 0
        elif qualifier == "trua" and 1 <= hour <= 2:
            hour += 12
        elif qualifier == "dem":
            hour = 0 if hour == 12 else hour + 12 if 6 <= hour < 12 else hour
        if hour > 23 or minute > 59 or (qualifier in {"sang", "am"} and hour >= 12) or (qualifier in {"chieu", "toi", "pm"} and hour < 12):
            result["time_error"] = "invalid_time"
        else:
            times.append(f"{hour:02d}:{minute:02d}")
    if len(set(times)) > 1:
        result["time_error"] = "ambiguous_time"
    elif times and "time_error" not in result:
        result["time"] = times[0]
    return result


def valid_vehicle_plate(value: str | None) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return 5 <= len(compact) <= 12 and sum(character.isdigit() for character in compact) >= 2


def valid_visitor_name(value: str | None) -> bool:
    return sum(character.isalpha() for character in str(value or "")) >= 2


class ToolService:
    def __init__(self, store: Store, retriever: RegulationRetriever):
        self.store = store
        self.retriever = retriever

    def get_resident_profile(self, resident_id: str) -> dict[str, Any]:
        profile = self.store.get_resident(resident_id)
        if not profile:
            raise ValueError("resident_not_found")
        return profile

    @staticmethod
    def _checklist_items(profile: dict[str, Any]) -> list[dict[str, Any]]:
        vehicle_labels = {"motorbike": "xe máy", "car": "ô tô"}
        selected = ["verify_residency"]
        if profile["resident_type"] == "owner":
            selected.append("temporary_residency")
        selected.extend(
            [
                "move_in_schedule",
                "resident_card",
                "vehicle_registration",
            ]
        )
        selected.append("account_registration")
        if profile["needs_internet"]:
            selected.append("internet_registration")
        selected.extend(["handover_inspection", "safety_briefing", "confirm_regulations"])
        reasons = {
            "verify_residency": "Mỗi cư dân mới cần xác nhận đúng khu và căn hộ.",
            "temporary_residency": "Đăng ký người ở cùng để BQL xem xét, hoặc xác nhận Chưa có nhu cầu nếu không thêm người.",
            "move_in_schedule": "Thống nhất lịch chuyển đồ, hoặc xác nhận Chưa có nhu cầu nếu chưa cần chuyển đồ.",
            "handover_inspection": "Cần ghi nhận hiện trạng căn hộ và chỉ số điện nước ban đầu.",
            "resident_card": "Thẻ cư dân là thủ tục bắt buộc khi tiếp nhận cư dân mới.",
            "account_registration": "Chủ sở hữu đăng ký tài khoản cho người thuê hoặc thành viên gia đình đã được BQL duyệt cư trú.",
            "confirm_regulations": "Cư dân mới cần đọc nội quy đang có hiệu lực.",
            "safety_briefing": "Cư dân cần biết lối thoát hiểm và hướng dẫn an toàn của tòa nhà.",
            "vehicle_registration": (
                f"Đăng ký {vehicle_labels.get(profile['vehicle'], 'phương tiện')} kèm giấy tờ, hoặc xác nhận Chưa có nhu cầu nếu chưa cần đăng ký xe."
            ),
            "internet_registration": "Hồ sơ khai báo có nhu cầu internet.",
        }
        items = [
            {
                "code": code,
                "title": PROCEDURES[code][0],
                "required": PROCEDURES[code][1],
                "reason": reasons[code],
            }
            for code in selected
        ]
        if profile["resident_type"] == "owner":
            items[0].update(title="Xác thực quyền sở hữu căn hộ", required=True, reason="Bắt buộc hoàn thành đầu tiên. Thông tin chủ sở hữu và căn hộ được khóa sau khi bạn xác nhận lưu.")
        else:
            items[0].update(title="Xác nhận thông tin cư trú", required=True, reason="Bắt buộc hoàn thành đầu tiên. Chỉ xác nhận thông tin BQL đã cấp; không thể tự sửa họ tên, tư cách hoặc căn hộ.")
        return items

    def build_checklist(self, resident_id: str) -> dict[str, Any]:
        profile = self.get_resident_profile(resident_id)
        items = self._checklist_items(profile)
        tasks = self.store.replace_active_checklist(resident_id, items)
        return {"profile_version": profile["profile_version"], "ruleset": "v3", "items": tasks}

    def get_onboarding_progress(self, resident_id: str) -> dict[str, Any]:
        profile = self.get_resident_profile(resident_id)
        tasks = self.store.list_tasks(resident_id)
        expected = self._checklist_items(profile)
        fields = ("code", "title", "required", "reason")
        if {tuple(task[field] for field in fields) for task in tasks} != {tuple(task[field] for field in fields) for task in expected}:
            tasks = self.build_checklist(resident_id)["items"]
        from src.services.resident_portal import reading_documents

        documents = reading_documents(self.store, resident_id, self.retriever.list_documents(profile["site_id"]))
        for task in tasks:
            if task["code"] == "verify_residency":
                task.update(title="Xác thực quyền sở hữu căn hộ" if profile["resident_type"] == "owner" else "Xác nhận thông tin cư trú", required=True, status="completed" if profile.get("ownership_confirmed_at") else "pending", reason="Bắt buộc hoàn thành đầu tiên. Chỉ xác nhận thông tin BQL đã cấp; không thể tự sửa họ tên, tư cách hoặc căn hộ.")
            if task["code"] == "confirm_regulations":
                task["status"] = "completed" if documents and all(item["acknowledged_at"] for item in documents) else "pending"
                task["acknowledged_count"] = sum(bool(item["acknowledged_at"]) for item in documents)
                task["document_count"] = len(documents)
        required = [task for task in tasks if task["required"]]
        completed = [task for task in required if task["status"] == "completed"]
        return {
            "profile_version": profile["profile_version"],
            "completed": len(completed),
            "total": len(required),
            "percent": round(100 * len(completed) / len(required)) if required else 0,
            "pending_tasks": [task for task in required if task["status"] != "completed"],
            "all_tasks": tasks,
        }

    def search_regulations(self, resident_id: str, query: str, category: str | None = None) -> dict[str, Any]:
        profile = self.get_resident_profile(resident_id)
        guidance = application_guidance(query, category)
        if guidance:
            # A verified policy for THIS building takes precedence over the demo.
            # Public laws and old sample markdown never establish its procedure.
            rows = [row for code in guide_codes(query, category)
                    for row in self.retriever.search(query, profile["site_id"], code)
                    if row.get("site_id") == profile["site_id"]
                    and row.get("source_type") == "building_policy"
                    and row.get("legal_status") == "verified"]
            if not rows:
                return guidance
        else:
            selected_category = category or (legal_category(query) if is_legal_question(query) else None)
            rows = self.retriever.search(query, profile["site_id"], selected_category)
            if is_legal_question(query):
                rows = [row for row in rows if row.get("site_id") == "national"]
        if not rows:
            return {"answer_status": "no_valid_source", "requires_human": True, "sources": []}

        normalized_query = normalize(query)
        requested_claims = {
            "fee": (r"\b(phi|fee|cost|gia|muc phat|tien phat)\b", r"\d[\d.,]*\s*(vnd|dong)", r"\b(phi|fee|cost|gia|phat)\b"),
            "deadline": (r"(bao lau|may ngay|deadline|sla|how long)", r"\d+\s*(ngay|gio)", r"thoi han|xu ly|deadline|sla|hoan tat"),
        }
        sources = [
            {
                "source_id": row["source_id"],
                "title": row["title"],
                "version": row["version"],
                "site": row["site"],
                "effective_date": row["effective_date"],
                "section": row["section"],
                "score": row["score"],
                "excerpt": row["text"],
                "source_url": row.get("source_url", ""),
                "download_url": row.get("download_url", ""),
                "publisher": row.get("publisher", ""),
                "legal_status": row.get("legal_status", ""),
                "data_partition": "public_law" if row.get("site_id") == "national" else (
                    "demo_bql" if row.get("source_type") == "demo_bql" else "building_policy"
                ),
            }
            for row in rows
        ]
        site_procedure = not is_legal_question(query) and (category in {"verify_residency", "resident_card", "vehicle_registration", "visitor_registration", "move_in_schedule", "internet_registration"} or any(
            phrase in normalized_query for phrase in ("xac nhan thong tin cu tru", "xac nhan cu tru", "the cu dan", "resident card", "dang ky khach", "dang ky phuong tien", "dang ky xe", "dang ky internet", "lich chuyen do")
        ))
        site_rules = not is_legal_question(query) and any(phrase in normalized_query for phrase in (
            "noi quy", "quy dinh cua khu", "gio yen tinh", "ho boi", "be boi", "phong gym",
            "vat nuoi", "thu cung", "cho meo", "khach", "hanh lang", "giao hang",
        ))
        if (site_procedure or site_rules) and not any(row.get("site_id") == profile["site_id"] for row in rows):
            return {
                "answer_status": "insufficient_site_evidence", "requires_human": True, "sources": [],
                "source_notice": "Nguồn tìm được là pháp luật công khai, không phải hướng dẫn thủ tục đã xác minh của khu. Không suy ra hồ sơ, phí, thời hạn hoặc cách cấp thẻ của Ban quản lý từ các nguồn này.",
            }
        if (site_procedure or site_rules) and any(row.get("site_id") == profile["site_id"] for row in rows):
            sources = [source for source, row in zip(sources, rows, strict=True) if row.get("site_id") == profile["site_id"]]
            rows = [row for row in rows if row.get("site_id") == profile["site_id"]]
        topic_anchors = [group for triggers, group in TOPIC_ANCHORS if any(contains_phrase(normalized_query, phrase) for phrase in triggers)]
        meaningful_terms = set(TOKEN_RE.findall(normalized_query)) - SEARCH_STOPWORDS - {"phi", "gia", "phat", "fee", "cost", "deadline", "sla", "may", "ngay", "lau"}
        for claim, (pattern, evidence_pattern, label_pattern) in requested_claims.items():
            evidence = False
            for row in rows:
                for paragraph in re.split(r"\n\s*\n", normalize(row["text"])):
                    topical = any(contains_phrase(paragraph, phrase) for group in topic_anchors for phrase in group) if topic_anchors else bool(meaningful_terms & set(TOKEN_RE.findall(paragraph)))
                    if topical and re.search(label_pattern, paragraph) and re.search(evidence_pattern, paragraph) and not re.search(r"khong quy dinh|chua xac minh|not specified", paragraph):
                        evidence = True
            if re.search(pattern, normalized_query) and not evidence:
                return {
                    "answer_status": "insufficient_evidence",
                    "missing_claim": claim,
                    "requires_human": True,
                    "sources": sources,
                }
        needs_legal_check = any(source["legal_status"] == "not_verified" for source in sources)
        demo_sources = any(source["data_partition"] == "demo_bql" for source in sources)
        return {
            "answer_status": "supported", "requires_human": needs_legal_check, "sources": sources,
            "source_notice": (
                "Nguồn pháp luật công khai, không phải nội quy riêng của khu. "
                "Chưa kiểm tra đầy đủ hiệu lực và các sửa đổi; cần cơ quan có thẩm quyền xác nhận khi áp dụng."
            ) if needs_legal_check else (
                "Dữ liệu BQL minh họa của bản demo, không phải nội quy đã xác minh của một tòa nhà thật."
            ) if demo_sources else "",
            "data_partition": sources[0]["data_partition"],
            **({"application_guidance": guidance} if guidance else {}),
        }

    def update_task_status(self, resident_id: str, task_code: str, status: str) -> dict[str, Any]:
        if task_code == "confirm_regulations" and status == "completed":
            progress = self.get_onboarding_progress(resident_id)
            task = next((item for item in progress["all_tasks"] if item["code"] == task_code), None)
            if not task or task["status"] != "completed":
                raise ValueError("reading_confirmation_required")
        allowed = {"pending", "in_progress", "completed"}
        if status not in allowed:
            raise ValueError("invalid_task_status")
        if task_code != "confirm_regulations":
            raise ValueError("management_approval_required")
        return self.store.update_task(resident_id, task_code, status)

    def create_management_request(
        self,
        resident_id: str,
        request_type: str,
        vehicle_plate: str | None = None,
        idempotency_key: str | None = None,
        vehicle_type: str | None = None,
        visitor_name: str | None = None,
        visit_date: str | None = None,
        details: str | None = None,
        person_name: str | None = None,
        citizen_id: str | None = None,
        move_time: str | None = None,
        household_member_id: str | None = None,
        resident_type: str | None = None,
        pickup_date: str | None = None,
        pickup_time: str | None = None,
        *, preview_only: bool = False,
    ) -> dict[str, Any]:
        request_type = normalize_request_type(request_type) or ""
        person_name = normalize_person_name(person_name) if person_name is not None else None
        visitor_name = normalize_person_name(visitor_name) if visitor_name is not None else None
        if request_type not in REQUEST_TYPES:
            raise ValueError("unsupported_request_type")
        if request_type in {"temporary_residency", "account_registration"} and self.get_resident_profile(resident_id)["resident_type"] != "owner":
            raise ValueError("owner_role_required")
        member = None
        if request_type == "account_registration":
            member = self.store.account_registration_member(resident_id, household_member_id or "")
        if request_type == "temporary_residency" and resident_type not in {None, "tenant", "family_member"}:
            raise ValueError("invalid_resident_type")
        resolved_vehicle_type = normalize_vehicle_type(vehicle_type)
        if request_type == "vehicle_registration" and not resolved_vehicle_type:
            profile_vehicle = self.get_resident_profile(resident_id).get("vehicle")
            if profile_vehicle in {"motorbike", "car"}:
                resolved_vehicle_type = profile_vehicle
        missing_fields = []
        if request_type == "vehicle_registration" and not vehicle_plate:
            missing_fields.append("vehicle_plate")
        if request_type == "vehicle_registration" and not resolved_vehicle_type:
            missing_fields.append("vehicle_type")
        if request_type == "visitor_registration" and not visitor_name:
            missing_fields.append("visitor_name")
        if request_type == "visitor_registration" and not visit_date:
            missing_fields.append("visit_date")
        if request_type == "temporary_residency" and not (person_name or visitor_name):
            missing_fields.append("person_name")
        if request_type == "temporary_residency" and not citizen_id:
            missing_fields.append("citizen_id")
        if request_type == "move_in_schedule" and not visit_date:
            missing_fields.append("visit_date")
        if request_type == "move_in_schedule" and not move_time:
            missing_fields.append("move_time")
        if request_type == "resident_card" and (pickup_date or pickup_time):
            if not pickup_date:
                missing_fields.append("pickup_date")
            if not pickup_time:
                missing_fields.append("pickup_time")
        if request_type in {"internet_registration", "general_support"} and not details:
            missing_fields.append("details")
        if missing_fields:
            return {
                "created": False,
                "missing_fields": missing_fields,
                "next_action": "ask_resident",
            }
        invalid_fields: list[str] = []
        field_errors: dict[str, str] = {}
        if request_type == "vehicle_registration" and not valid_vehicle_plate(vehicle_plate):
            invalid_fields.append("vehicle_plate")
            field_errors["vehicle_plate"] = "invalid_vehicle_plate"
        normalized_visit_date = visit_date
        normalized_pickup_date = pickup_date
        if request_type != "resident_card":
            invalid_fields.extend(field for field, value in (("pickup_date", pickup_date), ("pickup_time", pickup_time)) if value is not None)
        elif pickup_date or pickup_time:
            normalized_pickup_date, date_error = normalize_visit_date(pickup_date)
            if date_error:
                invalid_fields.append("pickup_date")
                field_errors["pickup_date"] = date_error
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", pickup_time or ""):
                invalid_fields.append("pickup_time")
                field_errors["pickup_time"] = "invalid_pickup_time"
        if request_type == "visitor_registration":
            if not valid_visitor_name(visitor_name):
                invalid_fields.append("visitor_name")
                field_errors["visitor_name"] = "invalid_visitor_name"
            normalized_visit_date, date_error = normalize_visit_date(visit_date)
            if date_error:
                invalid_fields.append("visit_date")
                field_errors["visit_date"] = date_error
        if request_type == "temporary_residency" and not valid_visitor_name(person_name or visitor_name):
            invalid_fields.append("person_name")
            field_errors["person_name"] = "invalid_person_name"
        if request_type == "temporary_residency" and not re.fullmatch(r"\d{12}", re.sub(r"\s", "", citizen_id or "")):
            invalid_fields.append("citizen_id")
            field_errors["citizen_id"] = "invalid_citizen_id"
        if request_type == "move_in_schedule":
            normalized_visit_date, date_error = normalize_visit_date(visit_date)
            if date_error:
                invalid_fields.append("visit_date")
                field_errors["visit_date"] = date_error
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", move_time or ""):
                invalid_fields.append("move_time")
                field_errors["move_time"] = "invalid_move_time"
        if invalid_fields:
            return {
                "created": False,
                "invalid_fields": invalid_fields,
                "field_errors": field_errors,
                "next_action": "ask_resident",
            }
        payload = {}
        if member:
            payload = {**member, **({"details": details} if details else {})}
        elif request_type == "vehicle_registration":
            payload = {"vehicle_plate": vehicle_plate, "vehicle_type": resolved_vehicle_type}
        elif request_type == "card_replacement":
            payload = {"reason": details or "lost_card"}
        elif request_type == "visitor_registration":
            payload = {
                "visitor_name": visitor_name,
                "visit_date": normalized_visit_date,
                **({"details": details} if details else {}),
            }
        elif request_type == "temporary_residency":
            payload = {"person_name": person_name or visitor_name, "citizen_id": re.sub(r"\s", "", citizen_id or ""), "resident_type": resident_type or "family_member"}
            if visit_date:
                normalized_stay_start, date_error = normalize_visit_date(visit_date)
                if date_error:
                    return {"created": False, "invalid_fields": ["visit_date"], "field_errors": {"visit_date": date_error}, "next_action": "ask_resident"}
                payload["stay_start"] = normalized_stay_start
            if details:
                payload["details"] = details
        elif request_type == "move_in_schedule":
            payload = {"move_date": normalized_visit_date, "move_time": move_time}
            if details:
                payload["details"] = details
        elif request_type == "resident_card":
            if normalized_pickup_date and pickup_time:
                payload.update(pickup_date=normalized_pickup_date, pickup_time=pickup_time)
            if details:
                payload["details"] = details
        elif details:
            payload = {"details": details}
        if preview_only:
            return {"created": False, "preview": True, "request_type": request_type, "payload": payload}
        try:
            request = self.store.create_request(
                resident_id,
                request_type,
                payload,
                idempotency_key,
            )
        except ValueError as exc:
            if str(exc) != "idempotency_key_conflict":
                raise
            return {
                "created": False,
                "conflict": True,
                "error": "idempotency_key_conflict",
                "next_action": "do_not_retry",
            }
        deduplicated = bool(request.pop("deduplicated", False))
        deduplication_reason = request.pop("deduplication_reason", None)
        return {
            "created": not deduplicated,
            "duplicate": deduplicated,
            **({"deduplication_reason": deduplication_reason} if deduplicated else {}),
            **request,
        }

    def list_resident_requests(
        self,
        resident_id: str,
        request_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.get_resident_profile(resident_id)
        request_type = normalize_request_type(request_type)
        status = normalize_request_status(status)
        if request_type and request_type not in REQUEST_TYPES:
            raise ValueError("unsupported_request_type")
        if status and status not in REQUEST_STATUSES | REQUEST_STATUS_GROUPS:
            raise ValueError("invalid_request_status")

        query_status = status if status in REQUEST_STATUSES else None
        rows = self.store.list_requests(resident_id, request_type, query_status)
        if status == "active":
            rows = [item for item in rows if item["status"] in ACTIVE_REQUEST_STATUSES]
        elif status == "finished":
            rows = [item for item in rows if item["status"] in {"completed", "rejected", "cancelled_by_resident"}]
        safe_limit = max(1, min(limit, 50))
        counts_by_status = {
            current: sum(item["status"] == current for item in rows)
            for current in REQUEST_STATUSES
            if any(item["status"] == current for item in rows)
        }
        counts_by_type = {
            current: sum(item["request_type"] == current for item in rows)
            for current in REQUEST_TYPES
            if any(item["request_type"] == current for item in rows)
        }
        return {
            "resident_id": resident_id,
            "total": len(rows),
            "active": sum(item["status"] in ACTIVE_REQUEST_STATUSES for item in rows),
            "finished": sum(item["status"] in {"completed", "rejected", "cancelled_by_resident"} for item in rows),
            "counts_by_status": counts_by_status,
            "counts_by_type": counts_by_type,
            "requests": [{**item, "timeline": self.store.request_timeline(item["request_id"])} for item in rows[:safe_limit]],
            "truncated": len(rows) > safe_limit,
            "filters": {
                "request_type": request_type,
                "status": status,
            },
        }

    def get_request_status(self, resident_id: str, request_id: str) -> dict[str, Any]:
        normalized_request_id = request_id.strip().upper()
        request = self.store.get_request(normalized_request_id)
        if not request or request["resident_id"] != resident_id:
            return {"found": False}
        return {"found": True, **request, "timeline": self.store.request_timeline(normalized_request_id)}

    def cancel_management_request(self, resident_id: str, request_id: str) -> dict[str, Any]:
        return self.store.cancel_request(resident_id, request_id.strip().upper())

    def update_management_request(
        self, resident_id: str, request_id: str, details: str | None = None,
        citizen_id: str | None = None, move_date: str | None = None,
        move_time: str | None = None, resubmit: bool = False,
        *, person_name: str | None = None, visitor_name: str | None = None,
        visit_date: str | None = None, vehicle_plate: str | None = None,
        vehicle_type: str | None = None, replacement_details: str | None = None,
        pickup_date: str | None = None, pickup_time: str | None = None,
        expected_updated_at: str | None = None,
        preview_only: bool = False,
    ) -> dict[str, Any]:
        current = self.get_request_status(resident_id, request_id)
        if not current["found"]:
            raise ValueError("request_not_found")
        changes: dict[str, Any] = {}
        request_type = current["request_type"]
        for field, value, allowed_type in (
            ("person_name", person_name, "temporary_residency"),
            ("visitor_name", visitor_name, "visitor_registration"),
        ):
            if value is not None:
                name = normalize_person_name(value)
                if request_type != allowed_type or len(name) > 120 or not valid_visitor_name(name):
                    raise ValueError(f"invalid_{field}")
                changes[field] = name
        if details is not None and replacement_details is not None:
            raise ValueError("invalid_details")
        if details:
            changes["details"] = (current["payload"].get("details", "") + "\n" + details).strip()
        if replacement_details is not None:
            changes["details"] = replacement_details.strip()
        if "details" in changes and (len(changes["details"]) > 1000 or (
            request_type in {"general_support", "internet_registration"} and not changes["details"]
        )):
            raise ValueError("invalid_details")
        if citizen_id is not None:
            compact = re.sub(r"\s", "", citizen_id)
            if current["request_type"] != "temporary_residency" or not re.fullmatch(r"\d{12}", compact):
                raise ValueError("invalid_citizen_id")
            changes["citizen_id"] = compact
        if visit_date is not None:
            if request_type != "visitor_registration":
                raise ValueError("invalid_visit_date")
            normalized, error = normalize_visit_date(visit_date)
            if error or not normalized:
                raise ValueError(error or "invalid_visit_date")
            changes["visit_date"] = normalized
        if move_date is not None:
            if current["request_type"] not in {"move_in_schedule", "temporary_residency"}:
                raise ValueError("invalid_move_date")
            normalized, error = normalize_visit_date(move_date)
            if error or not normalized:
                raise ValueError(error or "invalid_move_date")
            changes["stay_start" if current["request_type"] == "temporary_residency" else "move_date"] = normalized
        if move_time is not None:
            if current["request_type"] != "move_in_schedule" or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", move_time):
                raise ValueError("invalid_move_time")
            changes["move_time"] = move_time
        if pickup_date is not None:
            if request_type != "resident_card":
                raise ValueError("invalid_pickup_date")
            normalized, error = normalize_visit_date(pickup_date)
            if error or not normalized:
                raise ValueError("invalid_pickup_date")
            changes["pickup_date"] = normalized
        if pickup_time is not None:
            if request_type != "resident_card" or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", pickup_time):
                raise ValueError("invalid_pickup_time")
            changes["pickup_time"] = pickup_time
        if "pickup_date" in changes or "pickup_time" in changes:
            updated_payload = {**current["payload"], **changes}
            if not updated_payload.get("pickup_date") or not updated_payload.get("pickup_time"):
                raise ValueError("pickup_schedule_incomplete")
        if vehicle_plate is not None:
            if request_type != "vehicle_registration" or len(vehicle_plate) > 20 or not valid_vehicle_plate(vehicle_plate):
                raise ValueError("invalid_vehicle_plate")
            changes["vehicle_plate"] = vehicle_plate.strip().upper()
        if vehicle_type is not None:
            normalized_type = normalize_vehicle_type(vehicle_type)
            if request_type != "vehicle_registration" or normalized_type not in {"motorbike", "car"}:
                raise ValueError("invalid_vehicle_type")
            changes["vehicle_type"] = normalized_type
        if preview_only:
            if current["status"] not in {"submitted", "in_review", "needs_info"}:
                raise ValueError("request_not_editable")
            if expected_updated_at is not None and expected_updated_at != current["updated_at"]:
                raise ValueError("request_version_conflict")
            return {**current, "payload": {**current["payload"], **changes}, "changes": changes}
        if request_type == "vehicle_registration" and (resubmit or current["status"] == "needs_info"):
            if self.store.get_vehicle_document(current["request_id"]) is None:
                raise ValueError("vehicle_document_required")
        return self.store.update_request(
            resident_id, request_id.strip().upper(), changes, resubmit,
            expected_updated_at=expected_updated_at,
        )

    def remove_temporary_resident(self, resident_id: str, request_id: str, *, expected_updated_at: str | None = None) -> dict[str, Any]:
        return self.store.remove_temporary_resident(resident_id, request_id.strip().upper(), expected_updated_at=expected_updated_at)

    @staticmethod
    def _household_roster(members: list[dict], requests: list[dict], *, can_remove: bool = False) -> list[dict]:
        hidden_accounts = {item["payload"].get("app_account_resident_id") for item in requests
                           if item["request_type"] == "temporary_residency" and item["payload"].get("hidden_from_household")}
        people = {
            member["resident_id"]: {
                "person_name": member["full_name"], "resident_type": member["resident_type"],
                "stay_start": None, "confirmed_at": member["ownership_confirmed_at"], "can_remove": False,
            }
            for member in members if member["ownership_confirmed_at"] and member["resident_id"] not in hidden_accounts
        }
        stays = sorted(
            (item for item in requests if item["request_type"] == "temporary_residency"
             and item["status"] in {"approved", "completed"} and not item["payload"].get("hidden_from_household")),
            key=lambda item: (item.get("residency_confirmed_at") or item["created_at"], item["request_id"]),
        )
        seen = set()
        for item in stays:
            payload = item["payload"]
            name = str(payload.get("person_name") or "").strip()
            identity = str(payload.get("citizen_id") or name).strip().casefold()
            if not name or identity in seen:
                continue
            seen.add(identity)
            account_id = payload.get("app_account_resident_id")
            if account_id in people:
                person = people[account_id]
                person["stay_start"] = payload.get("stay_start")
                person["confirmed_at"] = item.get("residency_confirmed_at") or person["confirmed_at"]
                person.update(request_id=item["request_id"], updated_at=item["updated_at"], can_remove=can_remove and person["resident_type"] != "owner")
            else:
                people["request:" + item["request_id"]] = {
                    "person_name": name, "resident_type": payload.get("resident_type", "family_member"),
                    "stay_start": payload.get("stay_start"), "confirmed_at": item.get("residency_confirmed_at"),
                    "request_id": item["request_id"], "updated_at": item["updated_at"], "can_remove": can_remove,
                }
        role_order = {"owner": 0, "family_member": 1, "tenant": 2}
        return sorted(people.values(), key=lambda person: (
            role_order.get(person["resident_type"], 3),
            datetime.fromisoformat(person["confirmed_at"]).timestamp() if person["confirmed_at"] else float("inf"),
            person["person_name"].casefold(),
        ))

    def get_household_dashboard(self, resident_id: str) -> dict[str, Any]:
        profile = self.get_resident_profile(resident_id)
        requests = self.store.list_requests(resident_id)
        members, household_requests = self.store.household_records(resident_id)
        member_profiles = {member["resident_id"]: member for member in members}
        today = datetime.now(timezone(timedelta(hours=7))).date()
        vehicles = []
        pending_vehicles = []
        for item in household_requests:
            if item["request_type"] != "vehicle_registration" or item["status"] in {"rejected", "cancelled_by_resident"} or item["payload"].get("hidden_from_household"):
                continue
            vehicle = {
                "request_id": item["request_id"],
                "vehicle_type": item["payload"].get("vehicle_type"),
                "vehicle_plate": item["payload"].get("vehicle_plate"),
                "registration_status": item["status"],
                "status": item["status"],
                "owner_name": member_profiles[item["resident_id"]]["full_name"],
                "owner_resident_type": member_profiles[item["resident_id"]]["resident_type"],
                "updated_at": item["updated_at"],
                "can_remove": profile["resident_type"] == "owner" or item["resident_id"] == resident_id,
            }
            (vehicles if item["status"] in {"approved", "completed"} else pending_vehicles).append(vehicle)
        owner = next((member for member in members if member["resident_type"] == "owner"), profile)
        stays = [
            {
                "request_id": item["request_id"],
                "person_name": item["payload"].get("person_name"),
                "status": item["status"],
                "stay_start": item["payload"].get("stay_start"),
                "resident_type": item["payload"].get("resident_type", "family_member"),
                "updated_at": item["updated_at"],
                "can_remove": profile["resident_type"] == "owner",
            }
            for item in household_requests if item["request_type"] == "temporary_residency" and item["status"] != "rejected" and not item["payload"].get("hidden_from_household")
        ]
        internet = next((item for item in requests if item["request_type"] == "internet_registration"), None)
        candidates = []
        if profile["resident_type"] == "owner":
            seen = set()
            for item in household_requests:
                if item["request_type"] != "temporary_residency":
                    continue
                try:
                    member = self.store.account_registration_member(resident_id, item["request_id"])
                except ValueError:
                    continue
                identity = item["payload"].get("citizen_id") or member["person_name"].strip().casefold()
                if identity not in seen:
                    seen.add(identity)
                    candidates.append(member)
        return {
            "resident_id": resident_id,
            "apartment": profile["apartment"],
            "as_of_date": today.isoformat(),
            "resident_type": profile["resident_type"],
            "can_add_occupants": profile["resident_type"] == "owner",
            "occupants": {
                "recorded_count": self.get_resident_profile(owner["resident_id"])["family_members"],
                "people": self._household_roster(members, household_requests, can_remove=profile["resident_type"] == "owner"),
                "temporary_residency_requests": stays,
            },
            "vehicles": vehicles,
            "pending_vehicles": pending_vehicles,
            "account_candidates": candidates,
            "internet": {
                "needed": profile["needs_internet"],
                "request_id": internet["request_id"] if internet else None,
                "status": internet["status"] if internet else "pending",
                "updated_at": internet["updated_at"] if internet else None,
                "can_cancel": bool(internet and internet["status"] not in {"rejected", "cancelled_by_resident"} and not internet["payload"].get("hidden_from_household")),
            },
        }

def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def make_langchain_tools(service: ToolService, resident_id: str):
    @tool("get_resident_profile")
    def get_resident_profile() -> str:
        """Read the current resident profile from the application database."""
        return _json(service.get_resident_profile(resident_id))

    @tool("search_regulations")
    def search_regulations(query: str, category: str = "") -> str:
        """Read application procedure guides, verified building policy, or public law.

        Use before explaining procedures. Application guides work without BQL
        documents. Preserve the source partition and demo labels; never infer
        building fees, hours or deadlines from public law or demo workflows.
        """
        return _json(service.search_regulations(resident_id, query, category or None))

    @tool("build_checklist")
    def build_checklist() -> str:
        """Build and persist a personalized checklist from the resident profile."""
        return _json(service.build_checklist(resident_id))

    @tool("get_onboarding_progress")
    def get_onboarding_progress() -> str:
        """Read official onboarding progress and pending tasks."""
        return _json(service.get_onboarding_progress(resident_id))

    @tool("update_task_status")
    def update_task_status(task_code: str, status: str) -> str:
        """Update a self-service task. Cannot complete tasks that require management approval."""
        return _json(service.update_task_status(resident_id, task_code, status))

    @tool("create_management_request")
    def create_management_request(
        request_type: str,
        vehicle_plate: str = "",
        vehicle_type: str = "",
        visitor_name: str = "",
        visit_date: str = "",
        details: str = "",
        person_name: str = "",
        idempotency_key: str = "",
        citizen_id: str = "",
        move_time: str = "",
        household_member_id: str = "",
        resident_type: str = "",
        pickup_date: str = "",
        pickup_time: str = "",
    ) -> str:
        """Create a supported resident request after explicit consent.

        For a person moving in or staying overnight use temporary_residency with
        person_name and a 12-digit citizen_id; never resident_card. For moving
        use move_in_schedule with visit_date and move_time in HH:MM. For complaints use
        general_support with details. Vehicle needs plate/type; a day visitor needs
        visitor_name and visit_date. Relative dates such as 'ngày mai' and 'ngày kia'
        may be passed literally; the backend resolves them in Asia/Bangkok.
        Dependent accounts require an eligible household_member_id returned by
        get_household_dashboard and owner consent; never include passwords.
        Resident cards accept an optional preferred pickup_date and pickup_time
        in HH:MM; provide both when a collection slot is requested. This is not
        a confirmed appointment. Never invent the resident's preferred slot.
        """
        if normalize_request_type(request_type) == "vehicle_registration":
            return _json({"created": False, "missing_fields": ["vehicle_document"], "next_action": "open_vehicle_form", "message": "Mở form Đăng ký thẻ xe để đính kèm ảnh giấy đăng ký, kiểm tra thông tin và xác nhận gửi BQL. Không tạo ticket xe từ nội dung chat."})
        return _json(
            service.create_management_request(
                resident_id,
                request_type,
                vehicle_plate or None,
                idempotency_key or None,
                vehicle_type or None,
                visitor_name or None,
                visit_date or None,
                details or None,
                person_name or None,
                citizen_id or None,
                move_time or None,
                household_member_id=household_member_id or None,
                resident_type=resident_type or None,
                pickup_date=pickup_date or None,
                pickup_time=pickup_time or None,
            )
        )

    @tool("get_request_status")
    def get_request_status(request_id: str) -> str:
        """Read the official status of a management request owned by this resident."""
        return _json(service.get_request_status(resident_id, request_id))

    @tool("list_resident_requests")
    def list_resident_requests(request_type: str = "", status: str = "", limit: int = 20) -> str:
        """List this resident's real request history and exact counts.

        Use this for questions about how many requests exist, request history,
        duplicates, latest requests, or status when no request ID was supplied.
        Optional filters use the internal request type and status values.
        """
        return _json(
            service.list_resident_requests(
                resident_id,
                request_type or None,
                status or None,
                limit,
            )
        )

    @tool("cancel_management_request")
    def cancel_management_request(request_id: str) -> str:
        """Cancel a ticket owned by this resident after their explicit request."""
        return _json(service.cancel_management_request(resident_id, request_id))

    @tool("update_management_request")
    def update_management_request(
        request_id: str, details: str = "", citizen_id: str = "",
        move_date: str = "", move_time: str = "", resubmit: bool = False,
        person_name: str = "", visitor_name: str = "", visit_date: str = "",
        vehicle_plate: str = "", vehicle_type: str = "", replacement_details: str | None = None,
        pickup_date: str = "", pickup_time: str = "",
    ) -> str:
        """Correct fields on an EXISTING ticket and optionally resubmit with resident consent. Names replace old names; replacement_details replaces the description, details appends a note. Read get_request_status first. Never create another ticket; use the old REQ ID."""
        return _json(service.update_management_request(
            resident_id, request_id, details or None, citizen_id or None,
            move_date or None, move_time or None, resubmit,
            person_name=person_name or None, visitor_name=visitor_name or None,
            visit_date=visit_date or None, vehicle_plate=vehicle_plate or None,
            vehicle_type=vehicle_type or None, replacement_details=replacement_details,
            pickup_date=pickup_date or None, pickup_time=pickup_time or None,
        ))

    @tool("get_household_dashboard")
    def get_household_dashboard() -> str:
        """Read the apartment's recorded occupant count, residency requests, vehicles with their registrants, and internet request."""
        return _json(service.get_household_dashboard(resident_id))

    return [
        get_resident_profile,
        search_regulations,
        build_checklist,
        get_onboarding_progress,
        update_task_status,
        create_management_request,
        list_resident_requests,
        get_request_status,
        cancel_management_request,
        update_management_request,
        get_household_dashboard,
    ]

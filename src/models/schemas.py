from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256, repr=False)


class NotificationsReadAll(BaseModel):
    through_event_id: int = Field(ge=0)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=8, max_length=256, repr=False)


class FirstPasswordChange(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    new_password: str = Field(min_length=8, max_length=256, repr=False)


class UsernameAvailability(BaseModel):
    username: str = Field(min_length=1, max_length=80)


class RegulationAcknowledgeItem(BaseModel):
    source_id: str = Field(min_length=1, max_length=120)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegulationBatchAcknowledge(BaseModel):
    documents: list[RegulationAcknowledgeItem] = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


class ResidentAccountCreate(BaseModel):
    account_request_id: str | None = Field(default=None, pattern=r"^REQ-[A-Z0-9]{8}$")
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=256, repr=False)
    full_name: str = Field(min_length=2, max_length=120, pattern=r".*\S.*")
    site_id: str = Field(min_length=1, max_length=60)
    building: str = Field(default="", max_length=80)
    apartment: str = Field(min_length=1, max_length=40, pattern=r".*\S.*")
    resident_type: Literal["owner", "tenant", "family_member"] = "owner"
    confirmed: Literal[True]
    idempotency_key: str = Field(min_length=8, max_length=100)


class RegulationAcknowledge(BaseModel):
    source_id: str = Field(min_length=1, max_length=120)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class ChatRequest(BaseModel):
    project_id: str = "moveinmate"
    actor_id: str = "R001"
    conversation_id: str = Field(min_length=3, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    language: Literal["auto", "vi", "en"] = "auto"


class Citation(BaseModel):
    source_id: str
    title: str
    version: str
    site: str
    effective_date: str
    section: str
    source_url: str = ""
    download_url: str = ""
    publisher: str = ""
    legal_status: str = ""
    data_partition: str = ""


class ToolTrace(BaseModel):
    tool: str
    summary: str


class ChatResponse(BaseModel):
    answer: str
    project_id: str
    actor_id: str
    conversation_id: str
    tools_used: list[ToolTrace] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    requires_human: bool = False
    policy_code: str | None = None
    a2ui: dict[str, Any] | None = None


class OwnershipConfirm(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: str = Field(min_length=2, max_length=120, pattern=r".*\S.*")
    building: str = Field(default="", max_length=80)
    apartment: str = Field(min_length=1, max_length=40, pattern=r".*\S.*")
    expected_profile_version: int = Field(ge=1)
    confirmed: Literal[True]
    immutable_acknowledged: Literal[True]


class ProfileUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: str | None = Field(default=None, min_length=2, max_length=120, pattern=r".*\S.*")
    resident_type: Literal["owner", "tenant", "family_member"] | None = None
    family_members: int = Field(ge=1, le=20)
    vehicle: Literal["none", "motorbike", "car"]
    needs_internet: bool


class ChecklistNoNeed(BaseModel):
    model_config = {"extra": "forbid"}
    confirmed: Literal[True]
    expected_updated_at: AwareDatetime
    expected_profile_version: int = Field(ge=1)


class HouseholdRemoval(BaseModel):
    model_config = {"extra": "forbid"}
    confirmed: Literal[True]
    expected_updated_at: AwareDatetime


class RequestCreate(BaseModel):
    # Legacy compatibility field. Authorization derives the owner from the
    # authenticated membership; a supplied value can never widen scope.
    resident_id: str | None = None
    request_type: Literal[
        "resident_card",
        "vehicle_registration",
        "card_replacement",
        "visitor_registration",
        "temporary_residency",
        "verify_residency",
        "move_in_schedule",
        "handover_inspection",
        "emergency_contact",
        "account_registration",
        "internet_registration",
        "general_support",
    ]
    vehicle_plate: str | None = Field(default=None, max_length=20)
    vehicle_type: Literal["motorbike", "car"] | None = None
    visitor_name: str | None = Field(default=None, max_length=120)
    visit_date: str | None = Field(default=None, max_length=80)
    details: str | None = Field(default=None, max_length=1000)
    residency_purpose: Literal["current", "supplement", "correction"] | None = None
    target_apartment: str | None = Field(default=None, max_length=40)
    target_building: str | None = Field(default=None, max_length=80)
    person_name: str | None = Field(default=None, max_length=120)
    citizen_id: str | None = Field(default=None, max_length=20)
    move_time: str | None = Field(default=None, max_length=5)
    pickup_date: str | None = Field(default=None, max_length=80)
    pickup_time: str | None = Field(default=None, max_length=5)
    household_member_id: str | None = Field(default=None, pattern=r"^REQ-[A-Z0-9]{8}$")
    resident_type: Literal["tenant", "family_member"] | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)


class RequestDraftChat(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=2000)
    request_type: str = Field(default="", max_length=40)
    draft: dict[str, str] = Field(default_factory=dict, max_length=16)
    document_attached: bool = False


class RequestUpdate(BaseModel):
    details: str | None = Field(default=None, max_length=1000)
    replacement_details: str | None = Field(default=None, max_length=1000)
    person_name: str | None = Field(default=None, min_length=2, max_length=120)
    visitor_name: str | None = Field(default=None, min_length=2, max_length=120)
    citizen_id: str | None = Field(default=None, max_length=20)
    visit_date: str | None = Field(default=None, max_length=80)
    move_date: str | None = Field(default=None, max_length=80)
    move_time: str | None = Field(default=None, max_length=5)
    pickup_date: str | None = Field(default=None, max_length=80)
    pickup_time: str | None = Field(default=None, max_length=5)
    vehicle_plate: str | None = Field(default=None, min_length=5, max_length=20)
    vehicle_type: Literal["motorbike", "car"] | None = None
    expected_updated_at: str | None = Field(default=None, max_length=50)
    resubmit: bool = False


class CorrectionChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    request_id: str | None = Field(default=None, pattern=r"^REQ-[A-Z0-9]{8}$")
    draft: RequestUpdate = Field(default_factory=RequestUpdate)
    expected_updated_at: str | None = Field(default=None, max_length=50)


class ManagementDecision(BaseModel):
    status: Literal["in_review", "needs_info", "approved", "rejected", "completed"]
    reason: str = Field(min_length=3, max_length=500)


class HealthResponse(BaseModel):
    status: Literal["ready", "configuration_required"]
    provider: str
    model: str
    agent: str
    rag: str
    database: str
    tools: list[str]
    speech_to_text: str = "configuration_required"
    text_to_speech: str = "configuration_required"


class TextToSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class ApiResult(BaseModel):
    data: dict[str, Any]

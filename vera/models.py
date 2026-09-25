from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field

# Health & Metadata
class ContextCounts(BaseModel):
    category: int = 0
    merchant: int = 0
    customer: int = 0
    trigger: int = 0

class HealthResponse(BaseModel):
    status: str = "ok"
    uptime_seconds: int = 0
    contexts_loaded: ContextCounts = Field(default_factory=ContextCounts)

class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str

# Context Ingestion
ScopeType = Literal["category", "merchant", "customer", "trigger"]

class ContextPushRequest(BaseModel):
    scope: ScopeType
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None

class ContextPushSuccess(BaseModel):
    accepted: bool = True
    ack_id: str
    stored_at: str

class ContextPushConflict(BaseModel):
    accepted: bool = False
    reason: str = "stale_version"
    current_version: int

class ContextPushError(BaseModel):
    accepted: bool = False
    reason: str
    details: Optional[str] = None

# Tick
class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)

class ProactiveAction(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: Literal["vera", "merchant_on_behalf"]
    trigger_id: str
    template_name: str
    template_params: List[str] = Field(default_factory=list)
    body: str
    cta: str
    suppression_key: str
    rationale: str

class TickResponse(BaseModel):
    actions: List[ProactiveAction] = Field(default_factory=list)

# Reply
class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: Literal["merchant", "customer", "user"] = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1

ReplyActionType = Literal["send", "wait", "end"]

class ReplyResponse(BaseModel):
    action: ReplyActionType
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None
    rationale: str

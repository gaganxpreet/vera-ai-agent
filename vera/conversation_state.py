import re
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

@dataclass
class ConversationTurn:
    role: str # "vera", "merchant", "customer"
    message: str
    action: Optional[str] = None # "send", "wait", "end"
    timestamp: str = ""

@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    turns: List[ConversationTurn] = field(default_factory=list)
    last_bot_message: str = ""
    last_trigger_id: Optional[str] = None
    last_suppression_key: Optional[str] = None
    auto_reply_count: int = 0
    no_response_count: int = 0
    intent: str = "INIT" # "INIT", "ACCEPTANCE", "DECLINE", "QUESTION", "HOSTILE", "AUTO_REPLY"
    mode: str = "PITCH" # "PITCH", "ACTION", "WAITING", "ENDED"
    status: str = "NEW" # "NEW", "PROACTIVE_SENT", "ENGAGED", "QUESTION", "ACTION_PENDING", "WAITING", "ENDED", "OPTED_OUT"
    opt_out: bool = False
    action_pending: bool = False
    first_outbound_sent: bool = False
    previous_body_hashes: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

# Common canned auto-reply regex patterns
AUTO_REPLY_PATTERNS = [
    r"thank you for (contacting|reaching|messaging)",
    r"our team will (respond|get back|contact)",
    r"we will get back to you",
    r"auto[- ]?reply",
    r"currently unavailable",
    r"thanks for reaching out",
    r"this is an automated",
    r"will reply shortly",
    r"we have received your message"
]

OPT_OUT_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"don'?t message me",
    r"no more messages",
    r"not interested",
    r"remove me",
    r"useless spam",
    r"spam",
    r"leave me alone",
    r"please stop"
]

ACCEPTANCE_PATTERNS = [
    r"\byes\b",
    r"\byep\b",
    r"\byeah\b",
    r"\bsure\b",
    r"\bok\b",
    r"\bokay\b",
    r"go ahead",
    r"let'?s do it",
    r"send it",
    r"send me",
    r"i want to join",
    r"proceed",
    r"please send",
    r"sounds good",
    r"draft the",
    r"schedule"
]

class ConversationStore:
    def __init__(self):
        self._conversations: Dict[str, ConversationState] = {}
        self._merchant_auto_replies: Dict[str, int] = {}

    def get_or_create(self, conversation_id: str, merchant_id: Optional[str] = None, customer_id: Optional[str] = None) -> ConversationState:
        now_iso = datetime.now(timezone.utc).isoformat()
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationState(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                created_at=now_iso,
                updated_at=now_iso
            )
        state = self._conversations[conversation_id]
        if merchant_id and not state.merchant_id:
            state.merchant_id = merchant_id
        if customer_id and not state.customer_id:
            state.customer_id = customer_id
        return state

    def record_auto_reply(self, merchant_id: Optional[str]) -> int:
        if not merchant_id:
            return 1
        count = self._merchant_auto_replies.get(merchant_id, 0) + 1
        self._merchant_auto_replies[merchant_id] = count
        return count

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        return self._conversations.get(conversation_id)

    def record_turn(self, conversation_id: str, role: str, message: str, action: Optional[str] = None):
        state = self.get_or_create(conversation_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        state.turns.append(ConversationTurn(role=role, message=message, action=action, timestamp=now_iso))
        state.updated_at = now_iso
        if role in ["vera", "merchant_on_behalf"]:
            state.last_bot_message = message
            state.first_outbound_sent = True
            body_hash = hashlib.sha256(message.strip().encode("utf-8")).hexdigest()
            state.previous_body_hashes.append(body_hash)

    def classify_inbound(self, message: str) -> str:
        text = message.lower().strip()
        
        # 1. Opt-out check
        for pattern in OPT_OUT_PATTERNS:
            if re.search(pattern, text):
                return "OPT_OUT"
                
        # 2. Auto-reply check
        for pattern in AUTO_REPLY_PATTERNS:
            if re.search(pattern, text):
                return "AUTO_REPLY"
                
        # 3. Acceptance / Commitment check
        # If clear acceptance phrases are present (e.g. "ok let's do it", "go ahead", "send it"),
        # prioritize ACTION transition even if followed by conversational inquiry like "What's next?"
        has_acceptance = any(re.search(pattern, text) for pattern in ACCEPTANCE_PATTERNS)
        has_parameter_question = any(w in text for w in ["can you", "could you", "is it possible", "how much", "what time", "schedule it for"])

        if has_acceptance and not has_parameter_question:
            return "ACCEPTANCE"

        # 4. Question / parameter check
        if "?" in text or any(w in text for w in ["what", "how", "why", "when", "where", "can you", "could you", "is it possible"]):
            return "QUESTION"

        if has_acceptance:
            return "ACCEPTANCE"
            
        return "GENERAL"

    def clear(self):
        self._conversations.clear()
        self._merchant_auto_replies.clear()

conversation_store = ConversationStore()

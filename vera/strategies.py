from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class TriggerStrategy:
    kind: str
    send_as: str # "vera" or "merchant_on_behalf"
    cta_type: str # "open_ended", "binary_yes_no", "none"
    template_name: str
    primary_goal: str
    compulsion_lever: str # "curiosity", "social_proof", "effort_externalization", "urgency", "reciprocity"

STRATEGY_REGISTRY: Dict[str, TriggerStrategy] = {
    "research_digest": TriggerStrategy(
        kind="research_digest",
        send_as="vera",
        cta_type="open_ended",
        template_name="vera_research_digest_v1",
        primary_goal="Share authoritative research trial/study relevant to merchant's patient/client cohort and offer next artifact",
        compulsion_lever="curiosity_reciprocity"
    ),
    "regulation_change": TriggerStrategy(
        kind="regulation_change",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_compliance_alert_v1",
        primary_goal="Notify merchant of regulatory/compliance deadline with precise citation and checklist action",
        compulsion_lever="urgency_loss_aversion"
    ),
    "recall_due": TriggerStrategy(
        kind="recall_due",
        send_as="merchant_on_behalf",
        cta_type="binary_yes_no",
        template_name="merchant_recall_reminder_v1",
        primary_goal="Polite, personalized service recall reminder with specific available slots and price",
        compulsion_lever="effort_externalization"
    ),
    "perf_dip": TriggerStrategy(
        kind="perf_dip",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_perf_dip_v1",
        primary_goal="Objective diagnosis of recent drop with peer benchmark and ready fix",
        compulsion_lever="loss_aversion_effort_externalization"
    ),
    "perf_spike": TriggerStrategy(
        kind="perf_spike",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_perf_spike_v1",
        primary_goal="Celebrate positive momentum with exact numbers and leverage into next action",
        compulsion_lever="social_proof_momentum"
    ),
    "renewal_due": TriggerStrategy(
        kind="renewal_due",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_renewal_reminder_v1",
        primary_goal="Remind merchant of plan renewal days and benefits to maintain continuity",
        compulsion_lever="urgency_continuity"
    ),
    "festival_upcoming": TriggerStrategy(
        kind="festival_upcoming",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_festival_campaign_v1",
        primary_goal="Offer high-intent festive campaign draft tailored to locality and category",
        compulsion_lever="timely_opportunity"
    ),
    "weather_heatwave": TriggerStrategy(
        kind="weather_heatwave",
        send_as="vera",
        cta_type="binary_yes_no",
        template_name="vera_weather_alert_v1",
        primary_goal="Capitalize on weather shift with immediate service/beverage offer",
        compulsion_lever="timely_opportunity"
    ),
    "competitor_opened": TriggerStrategy(
        kind="competitor_opened",
        send_as="vera",
        cta_type="open_ended",
        template_name="vera_competitor_alert_v1",
        primary_goal="Alert merchant to local competitive activity without panic and suggest differentiation",
        compulsion_lever="curiosity_competitive_awareness"
    ),
    "wedding_package_followup": TriggerStrategy(
        kind="wedding_package_followup",
        send_as="merchant_on_behalf",
        cta_type="binary_yes_no",
        template_name="merchant_bridal_followup_v1",
        primary_goal="Warm, timed follow-up following trial with exact days to event and slot hold",
        compulsion_lever="continuity_urgency"
    ),
    "curious_ask_due": TriggerStrategy(
        kind="curious_ask_due",
        send_as="vera",
        cta_type="open_ended",
        template_name="vera_curious_ask_v1",
        primary_goal="Low-friction operator question with immediate promise of artifact creation",
        compulsion_lever="reciprocity_effort_externalization"
    ),
    "scheduled_recurring": TriggerStrategy(
        kind="scheduled_recurring",
        send_as="vera",
        cta_type="open_ended",
        template_name="vera_weekly_checkin_v1",
        primary_goal="Weekly strategic touchpoint tailored to merchant current metrics",
        compulsion_lever="reciprocity"
    )
}

def derive_strategy_from_trigger(
    trigger: Dict[str, Any],
    category: Optional[Dict[str, Any]] = None,
    merchant: Optional[Dict[str, Any]] = None
) -> TriggerStrategy:
    """
    Dynamically derives an optimal engagement strategy for unseen/injected trigger kinds
    based on scope, payload keys, urgency, and category voice.
    """
    kind = trigger.get("kind", "unspecified_event")
    scope = trigger.get("scope", "merchant")
    cid = trigger.get("customer_id")
    payload = trigger.get("payload", {})
    payload_keys = set(k.lower() for k in payload.keys())
    urgency = trigger.get("urgency", 3)

    # 1. Determine send_as
    send_as = "merchant_on_behalf" if (scope == "customer" or cid) else "vera"

    # 2. Determine compulsion lever & CTA
    if any(k in payload_keys for k in ["deadline", "expires_at", "days_remaining", "deadline_iso"]):
        lever = "urgency_loss_aversion"
        cta = "binary_yes_no"
    elif any(k in payload_keys for k in ["delta_pct", "drop", "dip", "loss", "metric_drop"]):
        lever = "loss_aversion_effort_externalization"
        cta = "binary_yes_no"
    elif any(k in payload_keys for k in ["surge", "spike", "milestone", "review_count"]):
        lever = "social_proof_momentum"
        cta = "binary_yes_no"
    elif any(k in payload_keys for k in ["citation", "trial", "study", "research", "journal", "digest"]):
        lever = "curiosity_reciprocity"
        cta = "open_ended"
    elif any(k in payload_keys for k in ["competitor", "distance_km", "market"]):
        lever = "curiosity_competitive_awareness"
        cta = "open_ended" if send_as == "vera" else "binary_yes_no"
    elif any(k in payload_keys for k in ["festival", "weather", "match", "holiday"]):
        lever = "timely_opportunity"
        cta = "binary_yes_no"
    elif any(k in payload_keys for k in ["renewal", "expiry", "subscription", "plan"]):
        lever = "urgency_continuity"
        cta = "binary_yes_no"
    elif send_as == "merchant_on_behalf":
        lever = "effort_externalization"
        cta = "binary_yes_no"
    else:
        lever = "effort_externalization"
        cta = "open_ended"

    # 3. Determine goal based on vertical
    cat_tone = (category or {}).get("voice", {}).get("tone", "").lower()
    if "clinical" in cat_tone or "medical" in cat_tone:
        goal = f"Deliver objective clinical/operational decision support on {kind} with actionable peer-grounded next step."
    elif "motivational" in cat_tone or "coaching" in cat_tone:
        goal = f"Drive member engagement and accountability on {kind} with low-friction response hook."
    else:
        goal = f"Deliver timely operator engagement on {kind} with grounded data points and clear action proposal."

    safe_kind = kind.replace(" ", "_").lower()
    return TriggerStrategy(
        kind=kind,
        send_as=send_as,
        cta_type=cta,
        template_name=f"{send_as}_{safe_kind}_v1",
        primary_goal=goal,
        compulsion_lever=lever
    )

def get_strategy_for_kind(
    kind: str,
    scope: str = "merchant",
    trigger: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    merchant: Optional[Dict[str, Any]] = None
) -> TriggerStrategy:
    """
    Resolves known triggers via registry, and dynamically derives strategy for unknown triggers.
    """
    if kind in STRATEGY_REGISTRY:
        return STRATEGY_REGISTRY[kind]

    t_dict = trigger or {"kind": kind, "scope": scope}
    return derive_strategy_from_trigger(t_dict, category, merchant)


import uuid
from typing import Dict, Any, Optional
from vera.context_store import context_store
from vera.context_selector import project_context_for_trigger
from vera.strategies import TriggerStrategy, get_strategy_for_kind
from vera.prompt_builder import build_composition_prompt, build_reply_prompt
from vera.llm_client import llm_client
from vera.validator import output_validator
from vera.conversation_state import conversation_store
from vera.suppression import suppression_manager
from vera.models import ProactiveAction, ReplyResponse

class MessageComposer:
    async def compose_proactive_action(
        self,
        trigger_id: str,
        trigger: Dict[str, Any],
        strategy: TriggerStrategy,
        category: Optional[Dict[str, Any]] = None,
        merchant: Optional[Dict[str, Any]] = None,
        customer: Optional[Dict[str, Any]] = None
    ) -> Optional[ProactiveAction]:
        """
        Executes proactive composition flow:
        Resolve linked entities -> project context -> build prompt -> LLM (async) -> validate -> format
        """
        mid = trigger.get("merchant_id")
        cid = trigger.get("customer_id")
        
        if merchant is None and mid:
            merchant = context_store.get("merchant", mid)
        if category is None:
            category_slug = merchant.get("category_slug") if merchant else trigger.get("payload", {}).get("category")
            category = context_store.get("category", category_slug) if category_slug else None
        if customer is None and cid:
            customer = context_store.get("customer", cid)

        # Build projected minimal context view
        projection = project_context_for_trigger(category, merchant, trigger, customer)
        
        # Deterministic conversation identifier based on entity keys
        target_cid = cid or "all"
        conv_id = f"conv_{mid}_{target_cid}_{trigger_id}"
        conv_state = conversation_store.get_or_create(conv_id, merchant_id=mid, customer_id=cid)

        # Build prompt & query LLM (non-blocking async — does not stall the event loop)
        prompts = build_composition_prompt(projection, strategy, conv_state)
        raw_res = await llm_client.acompose_structured(projection, prompts["system"], prompts["user"])

        # Deterministic validation and repair
        validated = output_validator.validate_and_repair_proactive(
            raw_res,
            expected_send_as=strategy.send_as,
            expected_cta=strategy.cta_type,
            template_name=strategy.template_name,
            previous_body_hashes=conv_state.previous_body_hashes,
            projection=projection
        )
        if not validated:
            return None # Suppressed duplicate

        # Update conversation state & mark suppression
        suppression_key = trigger.get("suppression_key", f"trg:{trigger_id}")
        suppression_manager.mark_suppressed(suppression_key)
        
        conv_state.last_trigger_id = trigger_id
        conv_state.last_suppression_key = suppression_key
        conv_state.status = "PROACTIVE_SENT"
        conversation_store.record_turn(conv_id, role=validated["send_as"], message=validated["body"], action="send")

        return ProactiveAction(
            conversation_id=conv_id,
            merchant_id=mid or "unknown",
            customer_id=cid,
            send_as=validated["send_as"],
            trigger_id=trigger_id,
            template_name=validated["template_name"],
            template_params=validated.get("template_params", []),
            body=validated["body"],
            cta=validated["cta"],
            suppression_key=suppression_key,
            rationale=validated["rationale"]
        )

    async def compose_reply(
        self,
        conversation_id: str,
        merchant_id: Optional[str],
        customer_id: Optional[str],
        from_role: str,
        inbound_message: str,
        turn_number: int
    ) -> ReplyResponse:
        """
        Executes reply flow:
        State classification -> intent transition -> auto-reply check -> opt-out -> LLM (async) -> reply response
        """
        conv_state = conversation_store.get_or_create(conversation_id, merchant_id=merchant_id, customer_id=customer_id)
        
        # 0. Immediate guard: if conversation or participant is already opted out, cease all communication immediately
        if conv_state.opt_out or conv_state.status == "OPTED_OUT" or (conv_state.merchant_id and suppression_manager.is_opted_out(conv_state.merchant_id)):
            conv_state.opt_out = True
            conv_state.status = "OPTED_OUT"
            conversation_store.record_turn(conversation_id, role=from_role, message=inbound_message, action="end")
            return ReplyResponse(
                action="end",
                rationale="Merchant or conversation has already opted out. Ceased all further outreach."
            )

        # 1. Classify inbound intent
        inbound_intent = conversation_store.classify_inbound(inbound_message)
        conv_state.intent = inbound_intent

        # 2. Check for explicit Opt-out / Hostile Stop
        if inbound_intent == "OPT_OUT":
            conv_state.opt_out = True
            conv_state.status = "OPTED_OUT"
            if conv_state.merchant_id:
                suppression_manager.opt_out_merchant(conv_state.merchant_id)
            if conv_state.customer_id:
                suppression_manager.opt_out_customer(conv_state.customer_id)
            conversation_store.record_turn(conversation_id, role=from_role, message=inbound_message, action="end")
            return ReplyResponse(
                action="end",
                rationale="Merchant/customer explicitly opted out. Stopped conversation and suppressed future outreach."
            )

        # 3. Check for repeated Auto-reply
        if inbound_intent == "AUTO_REPLY":
            conv_state.auto_reply_count += 1
            conversation_store.record_auto_reply(conv_state.merchant_id)  # track for analytics only
            if conv_state.auto_reply_count >= 2:
                conv_state.status = "ENDED"
                conversation_store.record_turn(conversation_id, role=from_role, message=inbound_message, action="end")
                return ReplyResponse(
                    action="end",
                    rationale="Repeated canned auto-reply detected in this conversation. Ending gracefully to avoid spamming automated inbox."
                )
            else:
                conv_state.status = "WAITING"
                conversation_store.record_turn(conversation_id, role=from_role, message=inbound_message, action="wait")
                return ReplyResponse(
                    action="wait",
                    wait_seconds=14400,
                    rationale="Detected canned auto-reply phrasing ('Thank you for contacting'). Waiting 4 hours for a human operator."
                )

        # 4. Check for Acceptance / Commitment -> Switch to ACTION mode immediately
        if inbound_intent == "ACCEPTANCE":
            conv_state.mode = "ACTION"
            conv_state.status = "ACTION_PENDING"

        # Record incoming turn
        conversation_store.record_turn(conversation_id, role=from_role, message=inbound_message)

        # Resolve context for prompt
        merchant = context_store.get("merchant", conv_state.merchant_id) if conv_state.merchant_id else None
        category_slug = merchant.get("category_slug") if merchant else None
        category = context_store.get("category", category_slug) if category_slug else None
        customer = context_store.get("customer", conv_state.customer_id) if conv_state.customer_id else None

        # Build projected context: preserve original trigger details if available
        original_trigger = None
        if conv_state.last_trigger_id:
            original_trigger = context_store.get("trigger", conv_state.last_trigger_id)

        target_trigger = original_trigger or {"kind": "conversation_reply", "payload": {}}
        projection = project_context_for_trigger(category, merchant, target_trigger, customer)

        recent_turns = [{"role": t.role, "message": t.message} for t in conv_state.turns[-4:]]

        prompts = build_reply_prompt(inbound_message, inbound_intent, conv_state.mode, projection, recent_turns)
        raw_res = await llm_client.areply_structured(projection, inbound_message, prompts["system"], prompts["user"])

        # Deterministic validation
        validated = output_validator.validate_and_repair_reply(
            raw_res,
            conv_state.previous_body_hashes,
            projection=projection,
            inbound_message=inbound_message
        )
        
        # Record bot reply turn
        conversation_store.record_turn(
            conversation_id,
            role="vera",
            message=validated.get("body", ""),
            action=validated["action"]
        )

        return ReplyResponse(
            action=validated["action"],
            body=validated.get("body"),
            cta=validated.get("cta"),
            wait_seconds=validated.get("wait_seconds"),
            rationale=validated["rationale"]
        )

composer = MessageComposer()

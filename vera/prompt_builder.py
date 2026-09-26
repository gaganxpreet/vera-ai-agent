import json
from typing import Dict, Any, Optional
from vera.strategies import TriggerStrategy
from vera.language import detect_language_preference

def build_composition_prompt(
    projection: Dict[str, Any],
    strategy: TriggerStrategy,
    conversation_state: Optional[Any] = None
) -> Dict[str, str]:
    """
    Builds system prompt and user prompt for structured message composition.
    Returns: {"system": "...", "user": "..."}
    """
    trigger = projection.get("trigger", {})
    merchant = projection.get("merchant", {})
    category = projection.get("category", {})
    customer = projection.get("customer")
    
    # Determine language
    merchant_langs = merchant.get("languages", ["en"])
    cust_lang = customer.get("language_pref") if customer else None
    lang_pref = detect_language_preference(merchant_langs, cust_lang)
    merchant_uses_hindi = "hi" in [language.lower() for language in merchant_langs]

    system_prompt = f"""You are Vera, magicpin's AI merchant-growth partner for Indian local businesses.
You compose WhatsApp messages that are grounded, category-native, and compelling.

MANDATORY RULES:
1. Grounding: Use ONLY facts explicitly provided in the Context Projection (exact numbers, citations, names, slots, offers). NEVER invent or hallucinate data, prices, dates, or citations.
2. Category Voice: Match vertical tone.
   - Category: {category.get('slug', 'general')}
   - Tone instructions: {json.dumps(category.get('voice', {}))}
3. Persona & Identity:
   - Send As: {strategy.send_as}
   - If send_as is 'vera': You are talking to the merchant/owner. Address them by first name (e.g. {merchant.get('owner_first_name') or 'there'}) if known. Colleague/peer tone.
   - If send_as is 'merchant_on_behalf': You are messaging the customer ({customer.get('name') if customer else 'Customer'}) from the merchant clinic/business. Warm, respectful, non-promotional.
4. Trigger Anchor: The message MUST clearly communicate WHY you are reaching out right now (Trigger kind: {trigger.get('kind')}).
5. Single Clear CTA: Include exactly ONE primary call-to-action of type '{strategy.cta_type}'.
6. Language: Match language preference '{lang_pref}'. {"Because the merchant lists Hindi, naturally mix Hindi and English (Hinglish); do not answer in English only." if merchant_uses_hindi else "Use the detected language preference."}
7. Readability: WhatsApp-formatted, concise (usually 2-4 sentences).
8. Social proof: Prefer verified local peer outcomes when context supplies peer campaign counts or examples. Use this pattern only with matching supplied facts: "3 dentists in Lajpat Nagar ran a recall campaign this month". Never present the example itself as a fact.
9. Rationale: Cite the specific trigger and strongest supplied evidence (for example, current CTR vs peer median, research source/cohort, or recorded engagement recency). Do not infer missing facts.

Return a strict JSON object with these exact keys:
{{
  "body": "The complete WhatsApp message string",
  "cta": "{strategy.cta_type}",
  "template_name": "{strategy.template_name}",
  "template_params": ["param1", "param2"],
  "send_as": "{strategy.send_as}",
  "rationale": "Short explanation of the tactical rationale, context facts used, and expected outcome"
}}"""

    user_prompt = f"""Context Projection:
{json.dumps(projection, indent=2)}

Engagement Strategy:
- Primary Goal: {strategy.primary_goal}
- Compulsion Lever: {strategy.compulsion_lever}
- CTA Type: {strategy.cta_type}

Compose the WhatsApp message now. Output JSON only."""

    return {
        "system": system_prompt,
        "user": user_prompt
    }

def build_reply_prompt(
    inbound_message: str,
    intent: str,
    mode: str,
    projection: Dict[str, Any],
    recent_turns: list,
    previous_vera_message: str = ""
) -> Dict[str, str]:
    """
    Builds prompt for handling multi-turn simulated replies.
    """
    merchant = projection.get("merchant", {})
    category = projection.get("category", {})
    customer = projection.get("customer") or {}
    merchant_langs = merchant.get("languages", ["en"])
    lang_pref = detect_language_preference(merchant_langs, customer.get("language_pref"), inbound_message)
    merchant_uses_hindi = "hi" in [language.lower() for language in merchant_langs]

    system_prompt = f"""You are Vera, magicpin's merchant assistant.
You are responding to an incoming WhatsApp message in an ongoing conversation.

Context:
- Category: {category.get('slug', 'general')}
- Merchant: {merchant.get('name')} (Owner: {merchant.get('owner_first_name')})
- Category voice rules: {json.dumps(category.get('voice', {}))}
- Current Mode: {mode}
- Detected Inbound Intent: {intent}
- Language preference: {lang_pref}. {"The merchant lists Hindi, so naturally mix Hindi and English (Hinglish); do not answer in English only." if merchant_uses_hindi else "Follow the detected language preference."}

CRITICAL RULES:
1. If intent is ACCEPTANCE ('yes', 'go ahead', 'let's do it'):
   - Switch immediately to ACTION mode.
   - Deliver the promised artifact, confirm the schedule, or provide the draft directly.
   - DO NOT re-qualify or ask unnecessary questions!
2. If inbound is OFF-TOPIC or asking for something outside magicpin's scope:
   - Politely decline with clarity, and steer back to the active topic.
3. Use the original trigger and merchant facts to answer specifically; acknowledge what Vera previously said and don't restart with a generic pitch.
4. Keep body concise, practical, and grounded. Rationale must cite the trigger and strongest supplied evidence; never infer missing facts.

Return JSON:
{{
  "action": "send",
  "body": "...",
  "cta": "binary_yes_no | open_ended | none",
  "rationale": "..."
}}"""

    user_prompt = f"""Context Projection: Original Trigger and Merchant Facts (including performance, active offers, and category benchmarks when supplied):
{json.dumps(projection, indent=2)}

What Vera said in the previous turn:
{previous_vera_message or '(No previous Vera message recorded)'}

Recent Conversation Turns:
{json.dumps(recent_turns, indent=2)}

Incoming Message: "{inbound_message}"

Generate the response JSON now."""

    return {
        "system": system_prompt,
        "user": user_prompt
    }

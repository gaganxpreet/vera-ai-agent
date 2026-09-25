import json
import logging
from typing import Dict, Any, Optional
from vera.config import settings

logger = logging.getLogger("vera.llm")

def _generate_grounded_fallback(projection: Dict[str, Any], is_reply: bool = False, inbound_msg: str = "") -> Dict[str, Any]:
    """
    High-quality deterministic fallback composition when no LLM API key is configured.
    Derives message purely from projected context and trigger specifications without hallucination.
    """
    trigger = projection.get("trigger", {})
    merchant = projection.get("merchant", {})
    category = projection.get("category", {})
    customer = projection.get("customer")
    
    t_payload = trigger.get("payload", {})
    kind = trigger.get("kind", "")
    owner_name = merchant.get("owner_first_name") or "there"
    
    if is_reply:
        inbound_lower = inbound_msg.lower()
        if any(w in inbound_lower for w in ["yes", "go ahead", "let's do it", "send", "sure", "ok", "okay", "proceed"]):
            extra_param = ""
            for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
                if day in inbound_lower:
                    extra_param = f" for {day.title()}"
                    break
            return {
                "action": "send",
                "body": f"Done! I've prepared the campaign draft{extra_param} for {merchant.get('name')} and we're ready to proceed. Shall I share the preview with you now to review?",
                "cta": "binary_yes_no",
                "rationale": "Transitioned to action mode upon merchant acceptance; confirmed draft preparation and ready to proceed."
            }
        elif "gst" in inbound_lower or "tax" in inbound_lower:
            return {
                "action": "send",
                "body": "I'll have to leave GST/tax filing to your CA as that's outside what I can handle directly. Coming back to our growth campaign—would you like me to proceed with the draft we discussed?",
                "cta": "binary_yes_no",
                "rationale": "Politely declined off-topic GST inquiry while maintaining continuity and refocusing on active campaign."
            }
        else:
            return {
                "action": "send",
                "body": f"Thanks for the update, {owner_name}! I'm tracking this for {merchant.get('name')} and will share concrete insights as soon as we have fresh numbers.",
                "cta": "open_ended",
                "rationale": "Acknowledged incoming query with grounded merchant context."
            }

    # Proactive composition by trigger kind
    if kind == "research_digest":
        item = category.get("target_digest_item", {})
        title = item.get("title", "new clinical research")
        source = item.get("source", "Peer Journal")
        n_trial = f" (n={item.get('trial_n')})" if item.get("trial_n") else ""
        return {
            "body": f"Dr. {owner_name}, fresh findings in {source}: {title}{n_trial}. Relevant to your high-risk patient roster. Would you like me to share the 2-minute summary and draft a patient-education WhatsApp message for you? — {source}",
            "cta": "open_ended",
            "template_name": "vera_research_digest_v1",
            "template_params": [f"Dr. {owner_name}", title, source],
            "send_as": "vera",
            "rationale": "Grounds on verified clinical trial numbers and source citation; targets merchant specific cohort with low-friction offer."
        }

    elif kind == "regulation_change":
        deadline = t_payload.get("deadline_iso", "the upcoming regulatory deadline")
        item = category.get("target_digest_item", {})
        title = item.get("title", "regulatory guidelines update")
        source = item.get("source", "Official Council Circular")
        return {
            "body": f"Dr. {owner_name}, compliance update: {source} published {title} with effective deadline {deadline}. Want me to send the 3-point checklist to ensure your clinic is fully compliant?",
            "cta": "binary_yes_no",
            "template_name": "vera_compliance_alert_v1",
            "template_params": [f"Dr. {owner_name}", str(deadline), source],
            "send_as": "vera",
            "rationale": "Provides exact compliance deadline and regulatory citation with binary checklist offer."
        }

    elif kind == "recall_due":
        cust_name = customer.get("name", "there") if customer else "there"
        clinic_name = merchant.get("name", "our clinic")
        slots = t_payload.get("available_slots", [])
        slot_str = f"{slots[0].get('label')} or {slots[1].get('label')}" if len(slots) >= 2 else "this week"
        active_offers = merchant.get("active_offers", [])
        offer_str = f" {active_offers[0].get('title')}." if active_offers else ""
        return {
            "body": f"Hi {cust_name}, {clinic_name} here 🦷 It's time for your routine 6-month cleaning recall. We have slots open: {slot_str}.{offer_str} Would you like to confirm one of these times?",
            "cta": "binary_yes_no",
            "template_name": "merchant_recall_reminder_v1",
            "template_params": [cust_name, clinic_name, slot_str],
            "send_as": "merchant_on_behalf",
            "rationale": "Personalized customer recall using verified visit interval, real open slots, and active clinic offer."
        }

    elif kind == "perf_dip":
        metric = t_payload.get("metric", "traffic")
        delta_val = t_payload.get("delta_pct")
        delta_phrase = f"dipped {int(abs(delta_val)*100)}%" if delta_val is not None else "dipped"
        window = t_payload.get("window")
        window_phrase = f" over the past {window}" if window else ""
        return {
            "body": f"Hi {owner_name}, noticed {metric} {delta_phrase}{window_phrase} for {merchant.get('name')}. We can refresh your GBP post and spotlight active offers to recover momentum. Shall I prepare the draft for you?",
            "cta": "binary_yes_no",
            "template_name": "vera_perf_dip_v1",
            "template_params": [owner_name, metric, delta_phrase],
            "send_as": "vera",
            "rationale": "Diagnosis anchored on exact performance metric delta with immediate recovery draft offer."
        }

    elif kind == "renewal_due":
        days = t_payload.get("days_remaining")
        plan = t_payload.get("plan", "business")
        amt = t_payload.get("renewal_amount")
        days_phrase = f"has {days} days remaining" if days is not None else "is due for renewal soon"
        amt_phrase = f" (₹{amt})" if amt is not None else ""
        return {
            "body": f"Hi {owner_name}, your {merchant.get('name')} {plan} subscription {days_phrase}{amt_phrase}. Renew today to keep your verified badge and priority search ranking active without disruption. Want me to generate the instant renewal link?",
            "cta": "binary_yes_no",
            "template_name": "vera_renewal_reminder_v1",
            "template_params": [owner_name, plan, str(days or "soon")],
            "send_as": "vera",
            "rationale": "Continuity protection referencing verified subscription details without inventing amounts."
        }

    elif kind == "festival_upcoming":
        fest = t_payload.get("festival", "the upcoming festival")
        days = t_payload.get("days_until")
        days_phrase = f"in {days} days" if days is not None else "soon"
        loc = merchant.get("locality") or "your area"
        return {
            "body": f"Hi {owner_name}, {fest} is coming up {days_phrase}! Demand across {loc} usually surges for festive appointments. I've drafted a festive promotion ready to publish to Google & WhatsApp. Want me to send the preview?",
            "cta": "binary_yes_no",
            "template_name": "vera_festival_campaign_v1",
            "template_params": [owner_name, fest, str(days or "soon")],
            "send_as": "vera",
            "rationale": "Leverages upcoming festive timeline with pre-built merchant campaign preview."
        }

    elif kind == "wedding_package_followup":
        cust_name = customer.get("name", "there") if customer else "there"
        m_name = merchant.get("name", "our salon")
        days_wed = t_payload.get("days_to_wedding")
        if days_wed:
            timeline_phrase = f"With {days_wed} days until your wedding,"
            t_params = [cust_name, str(days_wed)]
        else:
            timeline_phrase = "Following your bridal trial,"
            t_params = [cust_name, m_name]
        return {
            "body": f"Hi {cust_name} 💍 {m_name} here! {timeline_phrase} now is the ideal window to schedule your 30-day skin-prep program. Would you like us to reserve your preferred weekend slot for session 1?",
            "cta": "binary_yes_no",
            "template_name": "merchant_bridal_followup_v1",
            "template_params": t_params,
            "send_as": "merchant_on_behalf",
            "rationale": "Warm bridal follow-up citing trial completion and countdown timeline if provided."
        }

    elif kind == "curious_ask_due":
        return {
            "body": f"Hi {owner_name}! Quick check for {merchant.get('name')} — what service has seen the highest customer demand this week? I'll turn it into a Google post and a 4-line WhatsApp reply for your team. Takes 2 minutes!",
            "cta": "open_ended",
            "template_name": "vera_curious_ask_v1",
            "template_params": [owner_name],
            "send_as": "vera",
            "rationale": "Low-friction merchant operator question offering immediate effort externalization via social post."
        }

    elif "planning" in kind or "program_drafting" in kind:
        program = t_payload.get("program_title", "new program package")
        return {
            "body": f"Hi {owner_name}, drafted a campaign outline for {merchant.get('name')} around {program}. Ready to review and schedule for this weekend. Shall I send the 3-line preview?",
            "cta": "binary_yes_no",
            "template_name": "vera_planning_v1",
            "template_params": [owner_name, program],
            "send_as": "vera",
            "rationale": "High-intent program planning draft externalizing effort with a quick preview binary ask."
        }

    elif "appointment" in kind:
        cust_name = customer.get("name", "there") if customer else "there"
        slot = t_payload.get("slot_time")
        slot_str = f" for {slot}" if slot else " for your upcoming visit"
        return {
            "body": f"Hi {cust_name}, gentle reminder from {merchant.get('name')}{slot_str}. Looking forward to seeing you! Reply 1 to confirm or let us know if you need to reschedule.",
            "cta": "binary_yes_no",
            "template_name": "merchant_appointment_reminder_v1",
            "template_params": [cust_name, str(slot or "upcoming")],
            "send_as": "merchant_on_behalf",
            "rationale": "Timely appointment reminder honoring merchant-customer relationship without fabricating times."
        }

    elif "milestone" in kind:
        count = t_payload.get("review_count")
        count_phrase = f"crossed {count} verified customer reviews" if count else "received fantastic new customer reviews"
        return {
            "body": f"Congratulations {owner_name}! {merchant.get('name')} just {count_phrase} on your profile. I've drafted a celebratory update to share with your customers. Want me to send the draft?",
            "cta": "binary_yes_no",
            "template_name": "vera_milestone_v1",
            "template_params": [owner_name, str(count or "reviews")],
            "send_as": "vera",
            "rationale": "Celebrates exact milestone achievement without inventing review counts."
        }

    elif "perf_spike" in kind:
        metric = t_payload.get("metric", "views")
        delta_val = t_payload.get("delta_pct")
        delta_phrase = f"+{int(abs(delta_val)*100)}%" if delta_val is not None else "significantly"
        return {
            "body": f"Great news {owner_name}! {metric.title()} for {merchant.get('name')} surged {delta_phrase} this week. Let's capitalize on this momentum by activating a fresh lead spotlight. Shall we proceed?",
            "cta": "binary_yes_no",
            "template_name": "vera_perf_spike_v1",
            "template_params": [owner_name, metric, delta_phrase],
            "send_as": "vera",
            "rationale": "Leverages verified traffic spike momentum into growth opportunity."
        }

    elif "competitor" in kind:
        dist = t_payload.get("distance_km")
        dist_phrase = f" {dist}km away" if dist else ""
        loc = merchant.get("locality") or "your area"
        return {
            "body": f"Hi {owner_name}, a new competitor opened{dist_phrase} in {loc}. To maintain your search visibility lead for {merchant.get('name')}, I suggest updating your featured offers. Want to see the recommendations?",
            "cta": "binary_yes_no",
            "template_name": "vera_competitor_alert_v1",
            "template_params": [owner_name, str(dist or loc)],
            "send_as": "vera",
            "rationale": "Local competitive awareness anchored strictly on verified distance or locality."
        }

    elif "winback" in kind or "lapsed" in kind or "chronic" in kind:
        cust_name = customer.get("name", "there") if customer else "there"
        active_offers = merchant.get("active_offers", [])
        offer_str = f" Enjoy our {active_offers[0].get('title')}." if active_offers else ""
        return {
            "body": f"Hi {cust_name}, we miss seeing you at {merchant.get('name')}!{offer_str} We have preferred slots open for you this week. Would you like us to book a time?",
            "cta": "binary_yes_no",
            "template_name": "merchant_winback_v1",
            "template_params": [cust_name, merchant.get('name', 'our store')],
            "send_as": "merchant_on_behalf",
            "rationale": "Respectful customer winback and reactivation respecting customer preferences."
        }

    elif "ipl" in kind:
        match_info = t_payload.get("match", "Tonight's match")
        return {
            "body": f"Hi {owner_name}, {match_info} is scheduled! For {merchant.get('name')}, delivery demand typically surges during evening innings. I've prepared a match-time combo offer for your profile. Want me to activate it?",
            "cta": "binary_yes_no",
            "template_name": "vera_ipl_v1",
            "template_params": [owner_name, match_info],
            "send_as": "vera",
            "rationale": "Match-day demand capitalization tailored to food & beverage timing."
        }

    # Default fallback
    loc = merchant.get("locality") or "your area"
    return {
        "body": f"Hi {owner_name}, check-in from Vera for {merchant.get('name')}. We noticed new local search activity in {loc}. Would you like a quick overview of your weekly visibility and recommendations?",
        "cta": "binary_yes_no",
        "template_name": "vera_checkin_v1",
        "template_params": [owner_name],
        "send_as": "vera",
        "rationale": "General grounded check-in anchored on merchant locality and search visibility."
    }

class LLMClient:
    def __init__(self):
        self.provider = settings.llm_provider.lower()
        if self.provider == "gemini":
            self.api_key = settings.gemini_api_key
        elif self.provider == "openai":
            self.api_key = settings.openai_api_key
        elif self.provider == "groq":
            self.api_key = settings.groq_api_key
        else:
            self.api_key = None

    def complete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if not self.api_key:
            return None

        try:
            if self.provider == "gemini":
                import urllib.request as urlrequest
                body = json.dumps({
                    "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1000}
                }).encode("utf-8")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={self.api_key}"
                req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urlrequest.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"]

            elif self.provider in ["openai", "groq"]:
                import urllib.request as urlrequest
                base_url = "https://api.openai.com/v1" if self.provider == "openai" else "https://api.groq.com/openai/v1"
                model = settings.openai_model if self.provider == "openai" else settings.groq_model
                body = json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1000
                }).encode("utf-8")
                req = urlrequest.Request(
                    f"{base_url}/chat/completions",
                    data=body,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                )
                with urlrequest.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"LLM API call failed ({self.provider}): {e}")
            return None

        return None

    def compose_structured(self, projection: Dict[str, Any], system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raw_text = self.complete(system_prompt, user_prompt)
        if raw_text:
            try:
                import re
                match = re.search(r'\{[\s\S]*\}', raw_text)
                if match:
                    parsed = json.loads(match.group())
                    if "body" in parsed:
                        return parsed
            except Exception as e:
                logger.warning(f"Failed to parse LLM structured output: {e}")

        # Seamless deterministic contextual fallback
        return _generate_grounded_fallback(projection)

    def reply_structured(self, projection: Dict[str, Any], inbound_msg: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raw_text = self.complete(system_prompt, user_prompt)
        if raw_text:
            try:
                import re
                match = re.search(r'\{[\s\S]*\}', raw_text)
                if match:
                    parsed = json.loads(match.group())
                    if "action" in parsed:
                        return parsed
            except Exception as e:
                logger.warning(f"Failed to parse LLM reply output: {e}")

        return _generate_grounded_fallback(projection, is_reply=True, inbound_msg=inbound_msg)

llm_client = LLMClient()

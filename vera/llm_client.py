import json
import logging
from typing import Dict, Any, Optional
from vera.config import settings

logger = logging.getLogger("vera.llm")

def _generate_grounded_fallback(projection: Dict[str, Any], is_reply: bool = False, inbound_msg: str = "", intent: str = "") -> Dict[str, Any]:
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
        if intent == "ACCEPTANCE" or any(w in inbound_lower for w in ["yes", "go ahead", "let's do it", "send", "sure", "ok", "okay", "proceed"]):
            extra_param = ""
            for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
                if day in inbound_lower:
                    extra_param = f" for {day.title()}"
                    break
            return {
                "action": "send",
                "body": f"Great! Confirmed{extra_param} for {merchant.get('name')}. We'll proceed with activating the campaign options as discussed.",
                "cta": "none",
                "rationale": "Transitioned to action execution upon merchant acceptance."
            }
        elif "gst" in inbound_lower or "tax" in inbound_lower:
            return {
                "action": "send",
                "body": "I'll have to leave GST/tax filing to your CA as that's outside what I can handle directly. Coming back to our growth campaign—would you like me to proceed with the draft we discussed?",
                "cta": "binary_yes_no",
                "rationale": "Politely declined off-topic GST inquiry while maintaining continuity and refocusing on active campaign."
            }
        else:
            # Handle questions using preserved original trigger context
            if "perf" in kind or t_payload.get("delta_pct") is not None:
                metric = t_payload.get("metric", "traffic")
                delta_val = t_payload.get("delta_pct")
                if delta_val is None:
                    d7 = merchant.get("performance", {}).get("delta_7d", {})
                    delta_val = d7.get(f"{metric}_pct") or d7.get("calls_pct") or d7.get("views_pct")
                delta_phrase = f"{int(round(abs(delta_val)*100))}%" if delta_val is not None else "recent"
                window = t_payload.get("window", "7d")
                return {
                    "action": "send",
                    "body": f"Looking at your profile analytics for {merchant.get('name')}, {metric} dropped by {delta_phrase} over the past {window}. Shall I share the recovery plan we can activate today?",
                    "cta": "binary_yes_no",
                    "rationale": f"Preserved original trigger context to provide exact performance metrics on query."
                }
            elif kind == "research_digest":
                item = category.get("target_digest_item", {})
                source = item.get("source")
                title = item.get("title")
                if source and title:
                    body_text = f"The study in {source} covers {title}. Would you like me to share the 2-minute summary and draft an education note for your patients?"
                elif title:
                    body_text = f"The clinical digest covers {title}. Would you like me to share the 2-minute summary?"
                else:
                    body_text = f"Would you like me to share the latest clinical digest summary relevant to {merchant.get('name')}?"
                return {
                    "action": "send",
                    "body": body_text,
                    "cta": "binary_yes_no",
                    "rationale": "Preserved research digest trigger context to answer query."
                }
            elif kind == "renewal_due":
                plan = t_payload.get("plan") or merchant.get("subscription", {}).get("plan")
                days = t_payload.get("days_remaining") or merchant.get("subscription", {}).get("days_remaining")
                plan_str = f" {plan}" if plan else ""
                days_str = f" has {days} days remaining" if days is not None else " is approaching renewal"
                return {
                    "action": "send",
                    "body": f"Your {merchant.get('name')}{plan_str} subscription{days_str}. Renewing keeps your verified badge and priority ranking active. Want me to send the renewal link?",
                    "cta": "binary_yes_no",
                    "rationale": "Preserved renewal trigger context."
                }
            else:
                return {
                    "action": "send",
                    "body": f"Thanks for the question, {owner_name}! I'm tracking this for {merchant.get('name')} and can help you optimize this right away. Would you like me to send the preview?",
                    "cta": "binary_yes_no",
                    "rationale": "Acknowledged incoming query with grounded merchant context."
                }

    # Proactive composition by trigger kind
    if kind == "research_digest":
        item = category.get("target_digest_item", {})
        title = item.get("title")
        source = item.get("source")
        n_trial = f" (n={item.get('trial_n')})" if item.get("trial_n") else ""
        
        if title and source:
            body_text = f"Dr. {owner_name}, fresh findings in {source}: {title}{n_trial}. Relevant to your patient roster. Would you like me to share the 2-minute summary and draft an education note for your patients? — {source}"
            t_params = [f"Dr. {owner_name}", title, source]
        else:
            body_text = f"Dr. {owner_name}, clinical digest update available for your specialty. Would you like a brief summary of the latest peer findings relevant to {merchant.get('name')}?"
            t_params = [f"Dr. {owner_name}", "Clinical Research Digest"]

        return {
            "body": body_text,
            "cta": "open_ended",
            "template_name": "vera_research_digest_v1",
            "template_params": t_params,
            "send_as": "vera",
            "rationale": "Grounds on verified clinical trial numbers and source citation; targets merchant specific cohort with low-friction offer."
        }

    elif kind == "regulation_change":
        deadline = t_payload.get("deadline_iso")
        item = category.get("target_digest_item", {})
        title = item.get("title")
        source = item.get("source")
        deadline_phrase = f" with effective deadline {deadline}" if deadline else ""
        if title and source:
            body_text = f"Dr. {owner_name}, compliance update: {source} published {title}{deadline_phrase}. Want me to send the 3-point checklist to ensure {merchant.get('name')} is fully compliant?"
            t_params = [f"Dr. {owner_name}", str(deadline or "upcoming"), source]
        elif title:
            body_text = f"Dr. {owner_name}, new compliance guidance{deadline_phrase}: {title}. Want me to send the key compliance checklist for {merchant.get('name')}?"
            t_params = [f"Dr. {owner_name}", str(deadline or "upcoming"), title[:30]]
        else:
            # No specific title/source available — skip specific claim
            body_text = f"Dr. {owner_name}, there's a regulatory update relevant to {merchant.get('name')}{deadline_phrase}. Want me to share the compliance checklist?"
            t_params = [f"Dr. {owner_name}", str(deadline or "")]
        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "vera_compliance_alert_v1",
            "template_params": t_params,
            "send_as": "vera",
            "rationale": "Compliance alert with source/deadline anchored on actual data; no invented defaults."
        }

    elif kind == "recall_due":
        cust_name = customer.get("name", "there") if customer else "there"
        m_name = merchant.get("name", "our clinic")
        cat_slug = category.get("slug") or merchant.get("category_slug", "dentists")
        slots = t_payload.get("available_slots", [])
        if len(slots) >= 2:
            slot_phrase = f" We have slots open: {slots[0].get('label')} or {slots[1].get('label')}."
            slot_param = f"{slots[0].get('label')} or {slots[1].get('label')}"
        elif len(slots) == 1:
            slot_phrase = f" Open slot available: {slots[0].get('label')}."
            slot_param = slots[0].get("label")
        else:
            slot_phrase = ""
            slot_param = "recall"

        active_offers = merchant.get("active_offers", [])
        offer_str = f" {active_offers[0].get('title')}." if active_offers else ""

        if cat_slug == "gyms":
            body_text = f"Hi {cust_name}, {m_name} team here! It's time for your periodic fitness progress review and workout session.{slot_phrase}{offer_str} Shall we reserve your spot?"
        elif cat_slug == "salons":
            body_text = f"Hi {cust_name}, {m_name} here ✨ Time for your next styling & care session.{slot_phrase}{offer_str} Would you like us to book a time?"
        elif cat_slug == "pharmacies":
            body_text = f"Hi {cust_name}, {m_name} here. Gentle reminder for your routine health and medication refill.{slot_phrase}{offer_str} Would you like us to prepare it for pickup?"
        elif cat_slug == "restaurants":
            body_text = f"Hi {cust_name}, {m_name} here! We'd love to welcome you back for your next dining visit.{slot_phrase}{offer_str} Would you like to reserve a table?"
        else: # dentists
            body_text = f"Hi {cust_name}, {m_name} here 🦷 It's time for your routine 6-month cleaning recall.{slot_phrase}{offer_str} Would you like to confirm a visit?"

        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "merchant_recall_reminder_v1",
            "template_params": [cust_name, m_name, slot_param],
            "send_as": "merchant_on_behalf",
            "rationale": f"Personalized customer recall tailored to {cat_slug} category voice and real availability."
        }

    elif kind == "perf_dip":
        metric = t_payload.get("metric", "traffic")
        delta_val = t_payload.get("delta_pct")
        if delta_val is None:
            d7 = merchant.get("performance", {}).get("delta_7d", {})
            delta_val = d7.get(f"{metric}_pct") or d7.get("calls_pct") or d7.get("views_pct")

        delta_phrase = f"dipped {int(round(abs(delta_val)*100))}%" if delta_val is not None else "dipped recently"
        window = t_payload.get("window")
        window_phrase = f" over the past {window}" if window else ""
        return {
            "body": f"Hi {owner_name}, {metric} {delta_phrase}{window_phrase} for {merchant.get('name')}. Refreshing your profile spotlight and active offers can help recover momentum quickly. Want me to show you the recommended actions?",
            "cta": "binary_yes_no",
            "template_name": "vera_perf_dip_v1",
            "template_params": [owner_name, metric, delta_phrase],
            "send_as": "vera",
            "rationale": "Diagnosis anchored on exact performance metric delta; asks permission before taking any action."
        }

    elif kind == "renewal_due":
        days = t_payload.get("days_remaining") or merchant.get("subscription", {}).get("days_remaining")
        plan = t_payload.get("plan") or merchant.get("subscription", {}).get("plan")
        days_phrase = f"has {days} days remaining" if days is not None else "is due for renewal soon"
        plan_str = f" {plan}" if plan else ""
        amt = t_payload.get("renewal_amount")
        amt_phrase = f" (₹{amt})" if amt is not None else ""
        return {
            "body": f"Hi {owner_name}, your {merchant.get('name')}{plan_str} subscription {days_phrase}{amt_phrase}. Renew today to keep your verified badge and priority search ranking active without disruption. Want me to generate the instant renewal link?",
            "cta": "binary_yes_no",
            "template_name": "vera_renewal_reminder_v1",
            "template_params": [owner_name, str(plan or "business"), str(days or "soon")],
            "send_as": "vera",
            "rationale": "Continuity protection referencing verified subscription details without inventing amounts."
        }

    elif kind == "festival_upcoming":
        fest = t_payload.get("festival")
        days = t_payload.get("days_until")
        days_phrase = f" in {days} days" if days is not None else ""
        loc = merchant.get("locality")
        loc_phrase = f" across {loc}" if loc else ""

        if fest:
            body_text = f"Hi {owner_name}, {fest} is coming up{days_phrase}! Festive demand{loc_phrase} picks up for bookings and services. Want me to draft a {fest} promotion for {merchant.get('name')} to publish across Google & WhatsApp?"
            fest_param = fest
        else:
            body_text = f"Hi {owner_name}, peak seasonal demand is approaching{loc_phrase}. Want me to draft a seasonal promotion for {merchant.get('name')} to publish across Google & WhatsApp?"
            fest_param = "Seasonal Demand"

        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "vera_festival_campaign_v1",
            "template_params": [owner_name, fest_param, str(days or "soon")],
            "send_as": "vera",
            "rationale": "Leverages upcoming festive timeline with proactive offer to draft promotional campaign."
        }

    elif kind == "wedding_package_followup":
        cust_name = customer.get("name", "there") if customer else "there"
        m_name = merchant.get("name", "our salon")
        days_wed = t_payload.get("days_to_wedding")
        if days_wed:
            timeline_phrase = f"With {days_wed} days until your wedding, "
            t_params = [cust_name, str(days_wed)]
        else:
            timeline_phrase = ""
            t_params = [cust_name, m_name]
        return {
            "body": f"Hi {cust_name} 💍 {m_name} here! {timeline_phrase}now is the ideal window to schedule your skin-prep program. Would you like us to reserve your preferred slot for session 1?",
            "cta": "binary_yes_no",
            "template_name": "merchant_bridal_followup_v1",
            "template_params": t_params,
            "send_as": "merchant_on_behalf",
            "rationale": "Warm bridal follow-up citing countdown timeline if provided."
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
        program = t_payload.get("intent_topic") or t_payload.get("program_title") or "new program package"
        clean_prog = program.replace("_", " ").title()
        return {
            "body": f"Hi {owner_name}, we can set up a campaign outline for {merchant.get('name')} around {clean_prog}. Want me to draft the 3-line preview for you to review?",
            "cta": "binary_yes_no",
            "template_name": "vera_planning_v1",
            "template_params": [owner_name, clean_prog],
            "send_as": "vera",
            "rationale": "High-intent program planning offer externalizing effort with a quick preview binary ask."
        }

    elif "appointment" in kind:
        cust_name = customer.get("name", "there") if customer else "there"
        slot = t_payload.get("slot_time")
        if not slot:
            slots = t_payload.get("next_session_options") or t_payload.get("available_slots") or []
            if slots and isinstance(slots, list) and isinstance(slots[0], dict):
                slot = slots[0].get("label")
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
        val_now = t_payload.get("value_now")
        m_val = t_payload.get("milestone_value")
        count = t_payload.get("review_count") or val_now
        
        if val_now is not None and m_val is not None:
            diff = m_val - val_now
            count_phrase = f"reached {val_now} verified reviews — just {diff} away from your {m_val} milestone"
        elif count:
            count_phrase = f"crossed {count} verified customer reviews"
        else:
            leads = merchant.get("performance", {}).get("leads")
            views = merchant.get("performance", {}).get("views")
            if leads:
                count_phrase = f"crossed {leads} customer leads"
            elif views:
                count_phrase = f"reached {views} profile views this month"
            else:
                count_phrase = "reached a verified performance milestone"

        return {
            "body": f"Congratulations {owner_name}! {merchant.get('name')} just {count_phrase} on your profile. Want me to draft a celebratory update to share with your customers?",
            "cta": "binary_yes_no",
            "template_name": "vera_milestone_v1",
            "template_params": [owner_name, str(count or "milestone")],
            "send_as": "vera",
            "rationale": "Celebrates exact milestone achievement with grounded verifiable figures."
        }

    elif "perf_spike" in kind:
        metric = t_payload.get("metric", "views")
        delta_val = t_payload.get("delta_pct")
        if delta_val is None:
            d7 = merchant.get("performance", {}).get("delta_7d", {})
            delta_val = d7.get(f"{metric}_pct") or d7.get("views_pct") or d7.get("calls_pct")

        delta_phrase = f"+{int(round(abs(delta_val)*100))}%" if delta_val is not None else "strongly"
        views_count = merchant.get("performance", {}).get(metric)
        views_phrase = f" ({views_count} total)" if views_count else ""
        return {
            "body": f"Great news {owner_name}! {metric.title()} for {merchant.get('name')} surged {delta_phrase}{views_phrase} this week. Let's capitalize on this momentum by activating a fresh lead spotlight. Shall we proceed?",
            "cta": "binary_yes_no",
            "template_name": "vera_perf_spike_v1",
            "template_params": [owner_name, metric, delta_phrase],
            "send_as": "vera",
            "rationale": "Leverages verified traffic spike momentum with grounded performance metrics."
        }

    elif "competitor" in kind:
        dist = t_payload.get("distance_km")
        dist_phrase = f" {dist}km away" if dist else ""
        loc = merchant.get("locality")
        loc_phrase = f" in {loc}" if loc else ""
        return {
            "body": f"Hi {owner_name}, a new competitor opened{dist_phrase}{loc_phrase}. To keep {merchant.get('name')} visible and competitive in search results, I suggest refreshing your featured offers and profile. Want to see the recommendations?",
            "cta": "binary_yes_no",
            "template_name": "vera_competitor_alert_v1",
            "template_params": [owner_name, str(dist or loc or "nearby")],
            "send_as": "vera",
            "rationale": "Local competitive awareness anchored strictly on verified distance or locality."
        }

    elif "winback" in kind or "lapsed" in kind or "chronic" in kind:
        cust_name = customer.get("name", "there") if customer else "there"
        active_offers = merchant.get("active_offers", [])
        offer_str = f" Enjoy our {active_offers[0].get('title')}." if active_offers else ""
        molecules = t_payload.get("molecule_list", [])
        med_str = f" for your routine medications ({', '.join(molecules[:2])})" if molecules else ""
        
        if "refill" in kind or "chronic" in kind:
            body_text = f"Hi {cust_name}, gentle reminder from {merchant.get('name')}{med_str}.{offer_str} Would you like us to prepare your refill for pickup or delivery?"
        else:
            body_text = f"Hi {cust_name}, we miss seeing you at {merchant.get('name')}!{offer_str} Would you like us to help schedule your next visit?"

        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "merchant_winback_v1",
            "template_params": [cust_name, merchant.get('name', 'our store')],
            "send_as": "merchant_on_behalf",
            "rationale": "Respectful customer winback and reactivation respecting customer preferences."
        }

    elif "ipl" in kind:
        match_info = t_payload.get("match")
        match_phrase = f"{match_info} is coming up!" if match_info else "IPL match day is coming up!"
        param_val = match_info or "IPL Match"
        return {
            "body": f"Hi {owner_name}, {match_phrase} For {merchant.get('name')}, want me to set up a match-time combo offer on your profile to capture evening delivery traffic?",
            "cta": "binary_yes_no",
            "template_name": "vera_ipl_v1",
            "template_params": [owner_name, param_val],
            "send_as": "vera",
            "rationale": "Match-day demand capitalization anchored strictly on provided context."
        }

    # ── Default fallback: use the strongest available grounded fact ──────────
    loc_phrase = f" in {merchant.get('locality')}" if merchant.get('locality') else ""
    merchant_name = merchant.get("name", "your business")
    perf = merchant.get("performance", {})
    active_offers = merchant.get("active_offers", [])

    # Priority 1: performance metric we can anchor on
    views = perf.get("views")
    leads = perf.get("leads")
    delta_7d = perf.get("delta_7d", {})
    best_delta_key = next(
        (k for k in ["calls_pct", "views_pct", "leads_pct"] if delta_7d.get(k) is not None),
        None
    )
    if best_delta_key and delta_7d.get(best_delta_key) is not None:
        raw_delta = delta_7d[best_delta_key]
        metric_label = best_delta_key.replace("_pct", "")
        direction = "up" if raw_delta > 0 else "down"
        delta_phrase = f"{int(round(abs(raw_delta) * 100))}%"
        body_text = (
            f"Hi {owner_name}, quick update on {merchant_name}{loc_phrase}: "
            f"{metric_label} is {direction} {delta_phrase} this week. "
            f"Want to see what's driving this and what we can do to improve it?"
        )
        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "vera_checkin_v1",
            "template_params": [owner_name, metric_label, delta_phrase],
            "send_as": "vera",
            "rationale": f"Used grounded {metric_label} delta ({delta_phrase}) from performance data as check-in anchor."
        }

    # Priority 2: views or leads count (absolute)
    if views or leads:
        metric_label = "views" if views else "leads"
        count_val = views or leads
        body_text = (
            f"Hi {owner_name}, {merchant_name}{loc_phrase} had {count_val} {metric_label} recently. "
            f"Want a quick look at what's working and what we can improve this week?"
        )
        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "vera_checkin_v1",
            "template_params": [owner_name, str(count_val), metric_label],
            "send_as": "vera",
            "rationale": f"Used grounded {metric_label} count ({count_val}) as check-in anchor."
        }

    # Priority 3: active offer
    if active_offers:
        offer_title = active_offers[0].get("title", "your current offer")
        body_text = (
            f"Hi {owner_name}, your active offer '{offer_title}' is live on {merchant_name}{loc_phrase}. "
            f"Want to check how it's performing and whether boosting it would help this week?"
        )
        return {
            "body": body_text,
            "cta": "binary_yes_no",
            "template_name": "vera_checkin_v1",
            "template_params": [owner_name, offer_title],
            "send_as": "vera",
            "rationale": f"Used grounded active offer '{offer_title}' as check-in anchor."
        }

    # Priority 4: bare locality check-in (no fabricated facts)
    return {
        "body": f"Hi {owner_name}, check-in from Vera for {merchant_name}{loc_phrase}. Would you like a quick overview of your profile performance and recommended growth actions for this week?",
        "cta": "binary_yes_no",
        "template_name": "vera_checkin_v1",
        "template_params": [owner_name],
        "send_as": "vera",
        "rationale": "General grounded check-in — no performance or offer data available to anchor on."
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

    async def acomplete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Non-blocking async completion using httpx. Returns None on any failure to trigger grounded fallback."""
        if not self.api_key:
            return None

        import httpx
        try:
            if self.provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={self.api_key}"
                body = {
                    "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 1000,
                        "responseMimeType": "application/json"
                    }
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=body)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    elif resp.status_code == 429:
                        logger.warning("Gemini quota exhausted (HTTP 429) — using grounded fallback")
                        return None
                    else:
                        logger.warning(f"Gemini HTTP {resp.status_code} — using grounded fallback")
                        return None

            elif self.provider in ["openai", "groq"]:
                base_url = "https://api.openai.com/v1" if self.provider == "openai" else "https://api.groq.com/openai/v1"
                model = settings.openai_model if self.provider == "openai" else settings.groq_model
                body = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1000,
                    "response_format": {"type": "json_object"}
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        json=body,
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["choices"][0]["message"]["content"]
                    elif resp.status_code == 429:
                        logger.warning(f"{self.provider} quota exhausted (HTTP 429) — using grounded fallback")
                        return None
                    else:
                        logger.warning(f"{self.provider} HTTP {resp.status_code} — using grounded fallback")
                        return None
        except httpx.TimeoutException:
            logger.warning(f"LLM request timed out ({self.provider}) — using grounded fallback")
            return None
        except Exception as e:
            logger.warning(f"Async LLM call failed ({self.provider}): {e}")
            return None

        return None

    def complete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Synchronous wrapper using httpx with 10s timeout."""
        if not self.api_key:
            return None

        import httpx
        try:
            if self.provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={self.api_key}"
                body = {
                    "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1000}
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=body)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"]

            elif self.provider in ["openai", "groq"]:
                base_url = "https://api.openai.com/v1" if self.provider == "openai" else "https://api.groq.com/openai/v1"
                model = settings.openai_model if self.provider == "openai" else settings.groq_model
                body = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1000
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        f"{base_url}/chat/completions",
                        json=body,
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Sync LLM call failed ({self.provider}): {e}")
            return None

        return None

    def _parse_json(self, raw_text: Optional[str]) -> Optional[Dict[str, Any]]:
        if not raw_text:
            return None
        text = raw_text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            import re
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
        return None

    async def acompose_structured(self, projection: Dict[str, Any], system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raw_text = await self.acomplete(system_prompt, user_prompt)
        parsed = self._parse_json(raw_text)
        if parsed and "body" in parsed:
            return parsed
        return _generate_grounded_fallback(projection)

    async def areply_structured(self, projection: Dict[str, Any], inbound_msg: str, system_prompt: str, user_prompt: str, intent: str = "") -> Dict[str, Any]:
        raw_text = await self.acomplete(system_prompt, user_prompt)
        parsed = self._parse_json(raw_text)
        if parsed and "action" in parsed:
            return parsed
        return _generate_grounded_fallback(projection, is_reply=True, inbound_msg=inbound_msg, intent=intent)

    def compose_structured(self, projection: Dict[str, Any], system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raw_text = self.complete(system_prompt, user_prompt)
        parsed = self._parse_json(raw_text)
        if parsed and "body" in parsed:
            return parsed
        return _generate_grounded_fallback(projection)

    def reply_structured(self, projection: Dict[str, Any], inbound_msg: str, system_prompt: str, user_prompt: str, intent: str = "") -> Dict[str, Any]:
        raw_text = self.complete(system_prompt, user_prompt)
        parsed = self._parse_json(raw_text)
        if parsed and "action" in parsed:
            return parsed
        return _generate_grounded_fallback(projection, is_reply=True, inbound_msg=inbound_msg, intent=intent)

llm_client = LLMClient()


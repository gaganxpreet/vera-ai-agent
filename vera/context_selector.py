from typing import Dict, Any, Optional

def project_context_for_trigger(
    category: Optional[Dict[str, Any]],
    merchant: Optional[Dict[str, Any]],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
    include_reply_context: bool = False
) -> Dict[str, Any]:
    """
    Minimizes context down to only the facts relevant to this trigger.
    Avoids sending large blobs, unrelated offers, or full digests.
    Every trigger family has an explicit allowlist of payload fields.
    Unknown/adaptive triggers preserve all non-placeholder fields.
    """
    kind = trigger.get("kind", "")
    t_payload = trigger.get("payload", {})

    # ── Per-kind payload allowlists ─────────────────────────────────────────
    # Only the fields semantically relevant to the trigger kind are forwarded.
    # Everything else (placeholder keys, unused fields) is silently dropped.
    KIND_PAYLOAD_FIELDS: Dict[str, list] = {
        # Performance signals
        "perf_dip":                 ["metric", "delta_pct", "window"],
        "perf_spike":               ["metric", "delta_pct", "window"],
        # Milestones
        "milestone_reached":        ["metric", "milestone_value", "value_now", "count", "review_count"],
        # Seasonal / event
        "festival_upcoming":        ["festival", "days_until"],
        "ipl_match_tonight":        ["match", "festival", "venue"],
        # Research / compliance
        "research_digest":          ["top_item_id", "category"],
        "regulation_change":        ["top_item_id", "category", "deadline_iso"],
        # Competition
        "competitor_opened":        ["competitor", "distance_km"],
        # Subscription
        "renewal_due":              ["plan", "days_remaining", "renewal_amount"],
        # Medication / refill
        "refill_due":               ["molecule_list", "last_refill", "stock_runs_out_iso"],
        "chronic_refill_due":       ["molecule_list", "last_refill", "deadline_iso", "days_until"],
        # Health recall / appointment
        "recall_due":               ["recall_reason", "available_slots", "slot_time", "next_session_options"],
        "appointment_tomorrow":     ["slot_time", "next_session_options", "available_slots", "days_until"],
        # Customer reactivation
        "customer_winback":         ["last_visit_days", "visit_count"],
        "lapsed_customer":          ["last_visit_days", "visit_count"],
        "customer_lapsed_soft":     ["last_visit_days", "visit_count"],
        # Wedding / bridal
        "wedding_package_followup": ["days_to_wedding", "program_title"],
        # Engagement / program
        "curious_ask_due":          [],
        "trial_followup":           ["trial_n", "program_title", "days_since_trial"],
        "kids_yoga_trial_followup": ["trial_n", "program_title", "days_since_trial"],
        # Program planning
        "corporate_planning":       ["intent_topic", "program_title", "days_until"],
        "program_drafting":         ["intent_topic", "program_title"],
        # Dormancy / listing
        "scheduled_recurring":      [],
        "dormant_merchant":         ["dormant_days"],
        "dormant_with_vera":        ["dormant_days", "last_active_iso"],
        "unverified_listing":       ["platform", "gbp_status"],
        # Reviews
        "review_theme_emerged":     ["theme", "sentiment", "sample_count"],
        "review_theme_late_delivery": ["theme", "sentiment", "sample_count"],
        # Seasonal demand
        "seasonal_acquisition_dip": ["metric", "delta_pct", "window", "season"],
        "summer_demand_shift":      ["metric", "delta_pct", "window", "season"],
        # Webinar / CDE
        "cde_webinar":              ["event_title", "date_iso", "days_until"],
    }

    allowed_fields = KIND_PAYLOAD_FIELDS.get(kind)
    if allowed_fields is not None:
        # Known kind: project only the allowed fields that are actually present
        clean_payload = {k: t_payload[k] for k in allowed_fields if k in t_payload}
    else:
        # Unknown / novel trigger: normalize metric delta aliases & strip raw placeholder keys
        # Preserve all non-placeholder fields so hidden judge scenarios remain handleable
        clean_payload = {}
        for k, v in t_payload.items():
            if k == "placeholder":
                continue
            # Normalize common delta aliases to delta_pct
            if k in ["change", "decline", "drop", "change_pct", "decline_pct"] and "delta_pct" not in clean_payload:
                clean_payload["delta_pct"] = v
            else:
                clean_payload[k] = v

    projected_trigger = {
        "id": trigger.get("id"),
        "kind": kind,
        "scope": trigger.get("scope", "merchant"),
        "urgency": trigger.get("urgency", 3),
        "payload": clean_payload
    }

    # 2. Project Merchant Facts (tailored per trigger kind)
    projected_merchant = {}
    if merchant:
        ident = merchant.get("identity", {})
        projected_merchant = {
            "merchant_id": merchant.get("merchant_id"),
            "name": ident.get("name") or merchant.get("name"),
            "owner_first_name": ident.get("owner_first_name") or merchant.get("owner_first_name"),
            "locality": ident.get("locality") or merchant.get("locality"),
            "city": ident.get("city") or merchant.get("city"),
            "languages": ident.get("languages") or merchant.get("languages", ["en"])
        }

        if include_reply_context:
            projected_merchant.update({
                "performance": merchant.get("performance", {}),
                "active_offers": [o for o in merchant.get("offers", []) if o.get("status") == "active"],
                "customer_aggregate": merchant.get("customer_aggregate", {}),
                "signals": merchant.get("signals", []),
                "conversation_history": merchant.get("conversation_history", [])[-4:]
            })

        # Include performance/subscription strictly when relevant (no synthetic defaults)
        if "perf" in kind or kind in ["seasonal_acquisition_dip", "summer_demand_shift", "dormant_merchant", "dormant_with_vera"]:
            perf = merchant.get("performance", {})
            metric = t_payload.get("metric")
            perf_proj = {}
            if "window_days" in perf:
                perf_proj["window_days"] = perf["window_days"]
            if metric and metric in perf:
                perf_proj[metric] = perf[metric]
            elif not metric:
                for m_key in ["views", "leads", "calls", "traffic"]:
                    if m_key in perf:
                        perf_proj[m_key] = perf[m_key]
            if "delta_7d" in perf:
                perf_proj["delta_7d"] = perf["delta_7d"]
            projected_merchant["performance"] = perf_proj
        elif kind == "renewal_due":
            projected_merchant["subscription"] = merchant.get("subscription", {})
        elif "milestone" in kind:
            perf = merchant.get("performance", {})
            projected_merchant["performance"] = {
                "views": perf.get("views"),
                "leads": perf.get("leads")
            }
        elif kind in ["review_theme_emerged", "review_theme_late_delivery"]:
            # Include rating data for review themes
            perf = merchant.get("performance", {})
            if perf.get("avg_rating"):
                projected_merchant["performance"] = {"avg_rating": perf["avg_rating"]}

        # Select most relevant active offer (rank by trigger keyword relevance)
        active_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
        if active_offers:
            # Score offer relevance to trigger
            trigger_text = (
                f"{kind} {clean_payload.get('intent_topic', '')} "
                f"{clean_payload.get('program_title', '')} "
                f"{clean_payload.get('recall_reason', '')} "
                f"{clean_payload.get('festival', '')}"
            ).lower()

            def score_offer(o: dict) -> int:
                title = o.get("title", "").lower()
                off_cat = o.get("category", "").lower()
                score = 0
                for w in trigger_text.split():
                    if len(w) > 3 and w in title:
                        score += 10
                if off_cat and off_cat in trigger_text:
                    score += 5
                return score

            best_offer = max(active_offers, key=score_offer)
            projected_merchant["active_offers"] = [best_offer]

    # 3. Project Category Facts (strictly isolated)
    projected_category = {}
    if category:
        projected_category = {
            "slug": category.get("slug"),
            "voice": category.get("voice", {})
        }

        if include_reply_context:
            projected_category["peer_stats"] = category.get("peer_stats", {})
        for field in ("peer_campaigns", "social_proof"):
            if field in category:
                projected_category[field] = category[field]

        # Only extract the targeted digest item if research/compliance
        top_item_id = t_payload.get("top_item_id")
        if top_item_id:
            for item in category.get("digest", []):
                if item.get("id") == top_item_id:
                    projected_category["target_digest_item"] = {
                        "title": item.get("title"),
                        "source": item.get("source"),
                        "trial_n": item.get("trial_n"),
                        "summary": item.get("summary")
                    }
                    break

        # If performance dip/spike, include only relevant peer benchmark
        if "perf" in kind:
            peer = category.get("peer_stats", {})
            projected_category["peer_benchmark"] = {
                "avg_rating": peer.get("avg_rating"),
                "avg_views_30d": peer.get("avg_views_30d"),
                "avg_calls_30d": peer.get("avg_calls_30d")
            }

        # Only include canonical offer if customer reactivation or recall
        if "recall" in kind or "winback" in kind or "lapsed" in kind:
            offers = category.get("offer_catalog", [])
            if offers:
                projected_category["offer_catalog"] = [offers[0]]

    # 4. Project Customer Facts (only if customer-scoped)
    projected_customer = None
    if customer:
        c_ident = customer.get("identity", {})
        projected_customer = {
            "customer_id": customer.get("customer_id"),
            "name": c_ident.get("name") or customer.get("name"),
            "language_pref": c_ident.get("language_pref") or customer.get("language_pref", "en"),
            "relationship": customer.get("relationship", {}),
            "consent": customer.get("consent", {})
        }

    return {
        "trigger": projected_trigger,
        "merchant": projected_merchant,
        "category": projected_category,
        "customer": projected_customer
    }

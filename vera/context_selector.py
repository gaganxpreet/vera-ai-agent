from typing import Dict, Any, Optional

def project_context_for_trigger(
    category: Optional[Dict[str, Any]],
    merchant: Optional[Dict[str, Any]],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Minimizes context down to only the facts relevant to this trigger.
    Avoids sending large blobs, unrelated offers, or full digests.
    """
    kind = trigger.get("kind", "")
    t_payload = trigger.get("payload", {})

    # ── Per-kind payload allowlists ─────────────────────────────────────────
    # Only the fields semantically relevant to the trigger kind are forwarded.
    # Everything else (placeholder keys, unused fields) is silently dropped.
    KIND_PAYLOAD_FIELDS: Dict[str, list] = {
        "perf_dip":             ["metric", "delta_pct", "window"],
        "perf_spike":           ["metric", "delta_pct", "window"],
        "milestone_reached":    ["metric", "milestone_value", "count"],
        "festival_upcoming":    ["festival", "days_until"],
        "ipl_match_tonight":    ["match", "festival"],
        "research_digest":      ["top_item_id", "category"],
        "competitor_opened":    ["competitor", "distance_km"],
        "renewal_due":          ["plan", "days_remaining"],
        "refill_due":           ["molecule_list", "last_refill", "stock_runs_out_iso"],
        "chronic_refill_due":   ["molecule_list", "last_refill", "deadline_iso"],
        "recall_due":           ["recall_reason", "available_slots"],
        "customer_winback":     ["last_visit_days", "visit_count"],
        "lapsed_customer":      ["last_visit_days"],
        "curious_ask_due":      [],
        "scheduled_recurring":  [],
        "dormant_merchant":     [],
        "unverified_listing":   [],
    }
    allowed_fields = KIND_PAYLOAD_FIELDS.get(kind)
    if allowed_fields is not None:
        # Known kind: project only the allowed fields that are actually present
        clean_payload = {k: t_payload[k] for k in allowed_fields if k in t_payload}
    else:
        # Unknown / novel trigger: strip placeholder keys but keep everything else
        # so adaptive handling has enough signal
        clean_payload = {k: v for k, v in t_payload.items() if k != "placeholder"}

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

        # Include performance/subscription strictly when relevant
        if "perf" in kind:
            perf = merchant.get("performance", {})
            metric = t_payload.get("metric", "views")
            projected_merchant["performance"] = {
                "window_days": perf.get("window_days", 30),
                metric: perf.get(metric),
                "delta_7d": perf.get("delta_7d", {})
            }
        elif kind == "renewal_due":
            projected_merchant["subscription"] = merchant.get("subscription", {})
        elif "milestone" in kind:
            perf = merchant.get("performance", {})
            projected_merchant["performance"] = {
                "views": perf.get("views"),
                "leads": perf.get("leads")
            }

        # Include relevant active offer (single best offer)
        active_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
        if active_offers:
            projected_merchant["active_offers"] = [active_offers[0]]

    # 3. Project Category Facts (strictly isolated)
    projected_category = {}
    if category:
        projected_category = {
            "slug": category.get("slug"),
            "voice": category.get("voice", {})
        }

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

        # If performance dip, include only relevant peer benchmark
        if "perf" in kind:
            peer = category.get("peer_stats", {})
            projected_category["peer_benchmark"] = {
                "avg_rating": peer.get("avg_rating"),
                "avg_views_30d": peer.get("avg_views_30d"),
                "avg_calls_30d": peer.get("avg_calls_30d")
            }

        # Only include canonical offer if customer reactivation or recall
        if "recall" in kind or "winback" in kind:
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

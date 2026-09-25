from typing import Dict, Any, Optional

def project_context_for_trigger(
    category: Optional[Dict[str, Any]],
    merchant: Optional[Dict[str, Any]],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Minimizes context down to only the facts relevant to this trigger.
    Never sends full 500KB blobs or entire digests.
    """
    kind = trigger.get("kind", "")
    t_payload = trigger.get("payload", {})

    # 1. Project Trigger Facts
    projected_trigger = {
        "id": trigger.get("id"),
        "kind": kind,
        "scope": trigger.get("scope"),
        "source": trigger.get("source"),
        "urgency": trigger.get("urgency"),
        "payload": t_payload
    }

    # 2. Project Merchant Facts
    projected_merchant = {}
    if merchant:
        ident = merchant.get("identity", {})
        projected_merchant = {
            "merchant_id": merchant.get("merchant_id"),
            "name": ident.get("name") or merchant.get("name"),
            "owner_first_name": ident.get("owner_first_name") or merchant.get("owner_first_name"),
            "city": ident.get("city") or merchant.get("city"),
            "locality": ident.get("locality") or merchant.get("locality"),
            "languages": ident.get("languages") or merchant.get("languages", ["en"]),
            "signals": merchant.get("signals", [])
        }
        
        # Include specific performance numbers if perf trigger or relevant
        if "perf" in kind or kind in ["renewal_due", "milestone_reached"]:
            projected_merchant["performance"] = merchant.get("performance", {})
            projected_merchant["subscription"] = merchant.get("subscription", {})
            
        if "customer" in kind or "recall" in kind:
            projected_merchant["customer_aggregate"] = merchant.get("customer_aggregate", {})

        # Include active offers
        active_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
        projected_merchant["active_offers"] = active_offers[:3]

    # 3. Project Category Facts
    projected_category = {}
    if category:
        projected_category = {
            "slug": category.get("slug"),
            "voice": category.get("voice", {}),
            "peer_stats": category.get("peer_stats", {})
        }
        
        # Only extract the relevant digest item if this is a research / regulation / compliance trigger
        top_item_id = t_payload.get("top_item_id")
        if top_item_id:
            digest_items = category.get("digest", [])
            for item in digest_items:
                if item.get("id") == top_item_id:
                    projected_category["target_digest_item"] = item
                    break

        # If festival / weather / seasonal
        if "festival" in kind or "weather" in kind or "seasonal" in kind:
            projected_category["seasonal_beats"] = category.get("seasonal_beats", [])
            projected_category["trend_signals"] = category.get("trend_signals", [])

        # Include canonical offer catalog sample
        projected_category["offer_catalog"] = category.get("offer_catalog", [])[:3]

    # 4. Project Customer Facts (only if customer-scoped)
    projected_customer = None
    if customer:
        c_ident = customer.get("identity", {})
        projected_customer = {
            "customer_id": customer.get("customer_id"),
            "name": c_ident.get("name") or customer.get("name"),
            "language_pref": c_ident.get("language_pref") or customer.get("language_pref", "en"),
            "relationship": customer.get("relationship", {}),
            "state": customer.get("state"),
            "preferences": customer.get("preferences", {}),
            "consent": customer.get("consent", {})
        }

    return {
        "trigger": projected_trigger,
        "merchant": projected_merchant,
        "category": projected_category,
        "customer": projected_customer
    }

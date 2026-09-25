from vera.context_store import context_store
from vera.composer import composer
from vera.strategies import get_strategy_for_kind

def compose(category: dict, merchant: dict, trigger: dict, customer: dict = None) -> dict:
    """
    Candidate composition interface as defined in challenge-brief.md:
    compose(category, merchant, trigger, customer?) -> {body, cta, send_as, suppression_key, rationale}
    """
    # Temporarily store to context_store to ensure state consistency
    if category and "slug" in category:
        context_store.upsert("category", category["slug"], 1, category)
    if merchant and "merchant_id" in merchant:
        context_store.upsert("merchant", merchant["merchant_id"], 1, merchant)
    if customer and "customer_id" in customer:
        context_store.upsert("customer", customer["customer_id"], 1, customer)
    if trigger and "id" in trigger:
        context_store.upsert("trigger", trigger["id"], 1, trigger)

    tid = trigger.get("id", "trg_direct")
    strategy = get_strategy_for_kind(
        trigger.get("kind", ""),
        trigger.get("scope", "merchant"),
        trigger=trigger,
        category=category,
        merchant=merchant
    )
    
    action = composer.compose_proactive_action(tid, trigger, strategy)
    if action:
        return {
            "body": action.body,
            "cta": action.cta,
            "send_as": action.send_as,
            "suppression_key": action.suppression_key,
            "rationale": action.rationale
        }

    # Contextual grounded fallback using projected data
    from vera.context_selector import project_context_for_trigger
    from vera.llm_client import _generate_grounded_fallback
    proj = project_context_for_trigger(category or {}, merchant or {}, trigger or {}, customer)
    fallback = _generate_grounded_fallback(proj)
    return {
        "body": fallback.get("body", "Hi there, checking in with an update on your profile performance."),
        "cta": fallback.get("cta", strategy.cta_type),
        "send_as": fallback.get("send_as", strategy.send_as),
        "suppression_key": trigger.get("suppression_key", f"supp_{tid}"),
        "rationale": fallback.get("rationale", "Grounded fallback message anchored on trigger facts.")
    }

if __name__ == "__main__":
    import uvicorn
    from vera.config import settings
    uvicorn.run("vera.app:app", host=settings.bot_host, port=settings.bot_port, reload=False)

from typing import Optional, Dict, Any
import asyncio
from vera.composer import composer
from vera.strategies import get_strategy_for_kind

def compose(category: dict, merchant: dict, trigger: dict, customer: dict = None) -> dict:
    """
    Candidate composition interface as defined in challenge-brief.md:
    compose(category, merchant, trigger, customer?) -> {body, cta, send_as, suppression_key, rationale}
    Pure stateless composition without mutating global context_store.
    """
    tid = trigger.get("id", "trg_direct")
    strategy = get_strategy_for_kind(
        trigger.get("kind", ""),
        trigger.get("scope", "merchant"),
        trigger=trigger,
        category=category,
        merchant=merchant
    )

    # compose_proactive_action is async; bridge it here with asyncio.run()
    # (this function is only called from tests / the judge's compose() harness, never from a live FastAPI route)
    action = asyncio.run(
        composer.compose_proactive_action(
            tid,
            trigger,
            strategy,
            category=category,
            merchant=merchant,
            customer=customer
        )
    )
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

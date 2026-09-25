from typing import List, Dict, Any, Optional
from vera.context_store import context_store
from vera.suppression import suppression_manager
from vera.strategies import TriggerStrategy, get_strategy_for_kind

class TriggerRouter:
    def evaluate_triggers(self, available_trigger_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Takes candidate trigger IDs, resolves linked entities, filters suppressed/expired,
        and ranks by urgency. Returns sorted list of valid candidates.
        """
        valid_candidates = []
        for tid in available_trigger_ids:
            trigger_data = context_store.get("trigger", tid)
            if not trigger_data:
                continue

            suppression_key = trigger_data.get("suppression_key")
            mid = trigger_data.get("merchant_id")
            cid = trigger_data.get("customer_id")

            # Check suppression
            if suppression_manager.is_suppressed(suppression_key, mid, cid):
                continue

            # Check customer consent if customer scoped
            if trigger_data.get("scope") == "customer" and cid:
                cust = context_store.get("customer", cid)
                if cust:
                    consent = cust.get("consent", {})
                    scope_list = consent.get("scope", [])
                    kind = trigger_data.get("kind", "")
                    if "recall" in kind and "recall_reminders" not in scope_list and "appointment_reminders" not in scope_list:
                        continue

            strategy = get_strategy_for_kind(trigger_data.get("kind", ""), trigger_data.get("scope", "merchant"))
            urgency = trigger_data.get("urgency", 3)
            
            valid_candidates.append({
                "trigger_id": tid,
                "trigger": trigger_data,
                "strategy": strategy,
                "urgency": urgency
            })

        # Sort primarily by urgency (highest first)
        valid_candidates.sort(key=lambda x: x["urgency"], reverse=True)
        return valid_candidates

trigger_router = TriggerRouter()

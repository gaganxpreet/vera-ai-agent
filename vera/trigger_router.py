from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from vera.context_store import context_store
from vera.suppression import suppression_manager
from vera.strategies import TriggerStrategy, get_strategy_for_kind

class TriggerRouter:
    def evaluate_triggers(self, available_trigger_ids: List[str], now: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Takes candidate trigger IDs, resolves linked entities, validates expiry against `now`,
        checks suppression and consent, and ranks triggers using multi-factor decision score.
        """
        now_dt: Optional[datetime] = None
        if now:
            try:
                clean_now = now.replace("Z", "+00:00")
                now_dt = datetime.fromisoformat(clean_now)
            except Exception:
                now_dt = None

        valid_candidates = []
        for tid in available_trigger_ids:
            trigger_data = context_store.get("trigger", tid)
            if not trigger_data:
                continue

            # 1. Expiry check against current simulation time
            expires_at = trigger_data.get("expires_at")
            if expires_at and now_dt:
                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if now_dt > exp_dt:
                        continue # Trigger has expired
                except Exception:
                    pass

            # 2. Effective from check
            effective_from = trigger_data.get("effective_from")
            if effective_from and now_dt:
                try:
                    eff_dt = datetime.fromisoformat(effective_from.replace("Z", "+00:00"))
                    if now_dt < eff_dt:
                        continue # Trigger is not yet active
                except Exception:
                    pass

            # 3. Linked Merchant Existence Check
            mid = trigger_data.get("merchant_id")
            if not mid:
                continue
            merchant = context_store.get("merchant", mid)
            if not merchant:
                continue # Cannot compose grounded action without merchant context

            category_slug = merchant.get("category_slug")
            category = context_store.get("category", category_slug) if category_slug else None

            # 4. Suppression & Opt-out Check
            suppression_key = trigger_data.get("suppression_key")
            cid = trigger_data.get("customer_id")
            if suppression_manager.is_suppressed(suppression_key, mid, cid):
                continue

            # 5. Customer Existence & Consent Check if customer scoped
            customer = None
            if trigger_data.get("scope") == "customer" or cid:
                if not cid:
                    continue
                customer = context_store.get("customer", cid)
                if not customer:
                    continue # Missing customer context for customer-facing communication

                consent = customer.get("consent", {})
                consent_status = str(consent.get("status", "active")).lower()
                if consent_status in ["revoked", "opted_out", "denied", "inactive"]:
                    continue # Explicit opt-out or revoked consent

                scope_list = consent.get("scope", [])
                kind = trigger_data.get("kind", "")
                if "recall" in kind and not any(s in scope_list for s in ["recall_reminders", "appointment_reminders", "all"]):
                    continue

            # 6. Strategy Resolution (with dynamic derivation for unseen triggers)
            strategy = get_strategy_for_kind(
                kind=trigger_data.get("kind", ""),
                scope=trigger_data.get("scope", "merchant"),
                trigger=trigger_data,
                category=category,
                merchant=merchant
            )

            # 7. Multi-Factor Priority Scoring
            # Priority = Urgency (0-50) + Business Impact (0-25) + Freshness (0-15) + Actionability (0-10)
            urgency_raw = trigger_data.get("urgency", 3)
            urgency_score = min(50, max(10, int(urgency_raw) * 10))

            kind = trigger_data.get("kind", "")
            if kind in ["regulation_change"]:
                impact_score = 25
            elif kind in ["recall_due", "renewal_due", "perf_dip"]:
                impact_score = 20
            elif kind in ["perf_spike", "festival_upcoming", "competitor_opened"]:
                impact_score = 15
            else:
                impact_score = 10

            freshness_score = 5
            if expires_at and now_dt:
                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    diff_hours = (exp_dt - now_dt).total_seconds() / 3600.0
                    if 0 < diff_hours <= 24:
                        freshness_score = 15 # Imminent deadline bonus
                    elif 24 < diff_hours <= 72:
                        freshness_score = 10
                except Exception:
                    pass

            payload = trigger_data.get("payload", {})
            actionability_score = 10 if payload else 2

            total_priority = urgency_score + impact_score + freshness_score + actionability_score

            valid_candidates.append({
                "trigger_id": tid,
                "trigger": trigger_data,
                "strategy": strategy,
                "urgency": urgency_raw,
                "priority": total_priority
            })

        # Sort primarily by composite priority score (highest first)
        valid_candidates.sort(key=lambda x: x["priority"], reverse=True)
        return valid_candidates

trigger_router = TriggerRouter()


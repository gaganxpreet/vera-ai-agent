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

            # 7. Multi-Factor Priority Scoring with Payload-level Business Intelligence
            # Priority = Urgency (10-50) + Impact (0-30) + Freshness (0-15) + Actionability (0-15) - LowValuePenalty
            urgency_raw = trigger_data.get("urgency", 3)
            urgency_score = min(50, max(10, int(urgency_raw) * 10))

            kind = trigger_data.get("kind", "")
            payload = trigger_data.get("payload", {})

            # Impact score: dynamic based on severity & commercial significance
            impact_score = 10
            delta_val = payload.get("delta_pct") or payload.get("change") or payload.get("drop")
            if delta_val is not None:
                # E.g. -0.40 drop -> high impact (up to 30 pts)
                severity = abs(float(delta_val))
                impact_score = min(30, int(20 + severity * 20))
            elif kind in ["regulation_change"]:
                impact_score = 28
            elif kind in ["renewal_due"]:
                days_rem = payload.get("days_remaining") or (merchant.get("subscription", {}).get("days_remaining"))
                impact_score = 25 if (days_rem is not None and days_rem <= 15) else 18
            elif kind in ["recall_due", "chronic_refill_due"]:
                impact_score = 22
            elif kind in ["perf_spike", "milestone_reached"]:
                impact_score = 18
            elif kind in ["festival_upcoming", "competitor_opened"]:
                impact_score = 16
            elif kind in ["curious_ask_due", "scheduled_recurring"]:
                impact_score = 8 # Lower priority for generic recurring checks

            # Freshness score: proximity to deadline / recent event
            freshness_score = 5
            if expires_at and now_dt:
                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    diff_hours = (exp_dt - now_dt).total_seconds() / 3600.0
                    if 0 < diff_hours <= 24:
                        freshness_score = 15 # Imminent deadline
                    elif 24 < diff_hours <= 72:
                        freshness_score = 10
                except Exception:
                    pass

            # Actionability: presence of actionable offers, trial citations, or explicit customer data
            actionability_score = 5
            if payload.get("available_slots") or payload.get("molecule_list") or payload.get("top_item_id"):
                actionability_score = 15
            elif payload.get("delta_pct") or payload.get("festival") or payload.get("intent_topic"):
                actionability_score = 12
            elif payload.get("placeholder"):
                actionability_score = 2 # Placeholder trigger without concrete facts

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


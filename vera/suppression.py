import time
from typing import Dict, Set, Optional

class SuppressionManager:
    def __init__(self):
        # Key: suppression_key -> expiry_timestamp (float) or True
        self._suppressed_keys: Dict[str, float] = {}
        # Explicitly opted out merchants / customers
        self._opted_out_merchants: Set[str] = set()
        self._opted_out_customers: Set[str] = set()
        self._simulated_now: Optional[float] = None

    def set_simulated_time(self, iso_timestamp: Optional[str]):
        """Synchronizes suppression expiration clock with challenge simulation time."""
        if iso_timestamp:
            try:
                from datetime import datetime
                clean = iso_timestamp.replace("Z", "+00:00")
                self._simulated_now = datetime.fromisoformat(clean).timestamp()
            except Exception:
                pass

    def _now(self) -> float:
        return self._simulated_now if self._simulated_now is not None else time.time()

    def is_suppressed(self, suppression_key: str, merchant_id: Optional[str] = None, customer_id: Optional[str] = None) -> bool:
        if merchant_id and merchant_id in self._opted_out_merchants:
            return True
        if customer_id and customer_id in self._opted_out_customers:
            return True
        if not suppression_key:
            return False
            
        now = self._now()
        if suppression_key in self._suppressed_keys:
            expiry = self._suppressed_keys[suppression_key]
            if expiry == 0 or expiry > now:
                return True
            else:
                del self._suppressed_keys[suppression_key]
        return False

    def mark_suppressed(self, suppression_key: str, ttl_seconds: float = 86400 * 7):
        if not suppression_key:
            return
        now = self._now()
        self._suppressed_keys[suppression_key] = now + ttl_seconds if ttl_seconds > 0 else 0

    def opt_out_merchant(self, merchant_id: str):
        if merchant_id:
            self._opted_out_merchants.add(merchant_id)

    def opt_out_customer(self, customer_id: str):
        if customer_id:
            self._opted_out_customers.add(customer_id)

    def is_opted_out(self, merchant_id: Optional[str] = None, customer_id: Optional[str] = None) -> bool:
        if merchant_id and merchant_id in self._opted_out_merchants:
            return True
        if customer_id and customer_id in self._opted_out_customers:
            return True
        return False

    def clear(self):
        self._suppressed_keys.clear()
        self._opted_out_merchants.clear()
        self._opted_out_customers.clear()

suppression_manager = SuppressionManager()

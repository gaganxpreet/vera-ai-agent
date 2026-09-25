import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from vera.models import ScopeType

class ContextRecord:
    def __init__(self, scope: ScopeType, context_id: str, version: int, payload: Dict[str, Any], stored_at: str):
        self.scope = scope
        self.context_id = context_id
        self.version = version
        self.payload = payload
        self.stored_at = stored_at

class ContextStore:
    def __init__(self):
        # Key: (scope, context_id) -> ContextRecord
        self._store: Dict[Tuple[str, str], ContextRecord] = {}
        self._lock = threading.RLock()

    def upsert(self, scope: ScopeType, context_id: str, version: int, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[int]]:
        """
        Store context by (scope, context_id).
        Rules:
        - same version already stored -> idempotent (accepted=True, but no-op update)
        - higher version -> atomically replaces old version (accepted=True)
        - lower version -> conflict (accepted=False, reason="stale_version", current_version=curr)
        Returns: (accepted: bool, ack_or_reason: str, current_version: Optional[int])
        """
        key = (scope, context_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            if key in self._store:
                current = self._store[key]
                if version < current.version:
                    return False, "stale_version", current.version
                if version == current.version:
                    # Idempotent no-op
                    return True, f"ack_{context_id}_v{version}", current.version
                
            # Higher version or new record
            self._store[key] = ContextRecord(
                scope=scope,
                context_id=context_id,
                version=version,
                payload=payload,
                stored_at=now_iso
            )
            return True, f"ack_{context_id}_v{version}", version

    def get(self, scope: ScopeType, context_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._store.get((scope, context_id))
            return record.payload if record else None

    def get_record(self, scope: ScopeType, context_id: str) -> Optional[ContextRecord]:
        with self._lock:
            return self._store.get((scope, context_id))

    def get_all(self, scope: ScopeType) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                cid: rec.payload
                for (sc, cid), rec in self._store.items()
                if sc == scope
            }

    def counts(self) -> Dict[str, int]:
        c = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (sc, _), _ in self._store.items():
                if sc in c:
                    c[sc] += 1
        return c

    def clear(self):
        with self._lock:
            self._store.clear()

context_store = ContextStore()

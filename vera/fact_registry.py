import re
from typing import Dict, Any, Set, List, Tuple

class FactRegistry:
    @staticmethod
    def extract_allowed_facts(projection: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts all verifiable facts (names, numbers, percentages, prices, sources, dates, offers)
        from projected context to serve as the ground-truth boundary.
        """
        merchant = projection.get("merchant", {})
        category = projection.get("category", {})
        customer = projection.get("customer") or {}
        trigger = projection.get("trigger", {})
        t_payload = trigger.get("payload", {})

        allowed_numbers: Set[str] = set()
        allowed_names: Set[str] = set()
        allowed_sources: Set[str] = set()
        allowed_offers: Set[str] = set()
        allowed_dates: Set[str] = set()

        # Merchant names, owner & locality
        if merchant.get("name"):
            allowed_names.add(merchant["name"].lower())
        if merchant.get("owner_first_name"):
            allowed_names.add(merchant["owner_first_name"].lower())
        if merchant.get("locality"):
            allowed_names.add(merchant["locality"].lower())
        if merchant.get("city"):
            allowed_names.add(merchant["city"].lower())
        if customer.get("name"):
            allowed_names.add(customer["name"].lower())

        # Category sources & digest
        item = category.get("target_digest_item", {})
        if item.get("source"):
            allowed_sources.add(item["source"].lower())
        if item.get("title"):
            allowed_sources.add(item["title"].lower())

        # Active & category offers
        for off in merchant.get("active_offers", []):
            if off.get("title"):
                allowed_offers.add(off["title"].lower())
        for off in category.get("offer_catalog", []):
            if off.get("title"):
                allowed_offers.add(off["title"].lower())

        # Extract dates from trigger payload (ISO dates, days, etc.)
        for k in ["deadline_iso", "last_refill", "stock_runs_out_iso", "expires_at", "window"]:
            v = t_payload.get(k)
            if v and isinstance(v, str):
                allowed_dates.add(v.lower())
                # also add YYYY-MM-DD substring if present
                for d_match in re.findall(r'\b\d{4}-\d{2}-\d{2}\b', v):
                    allowed_dates.add(d_match)

        # Numbers from payload & merchant data
        def collect_numbers(obj: Any):
            if isinstance(obj, (int, float)):
                allowed_numbers.add(str(obj))
                allowed_numbers.add(str(int(obj)))
                # If percentage delta e.g. -0.50 -> 50%
                if abs(obj) <= 1.0 and obj != 0:
                    pct_int = int(round(abs(obj) * 100))
                    allowed_numbers.add(str(pct_int))
                    allowed_numbers.add(f"{pct_int}%")
            elif isinstance(obj, str):
                for m in re.findall(r'\b\d+(?:\.\d+)?%?', obj):
                    allowed_numbers.add(m)
            elif isinstance(obj, dict):
                for v in obj.values():
                    collect_numbers(v)
            elif isinstance(obj, list):
                for v in obj:
                    collect_numbers(v)

        collect_numbers(t_payload)
        collect_numbers(merchant.get("performance", {}))
        collect_numbers(merchant.get("active_offers", []))
        collect_numbers(category.get("peer_stats", {}))
        collect_numbers(category.get("offer_catalog", []))
        collect_numbers(item)

        return {
            "allowed_numbers": allowed_numbers,
            "allowed_names": allowed_names,
            "allowed_sources": allowed_sources,
            "allowed_offers": allowed_offers,
            "allowed_dates": allowed_dates,
            "category_slug": category.get("slug", "")
        }

    @staticmethod
    def verify_grounding(body: str, facts: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validates that numbers, currency amounts, or percentages mentioned in body can be grounded.
        Returns (is_valid: bool, issues: List[str]).
        """
        allowed_nums = facts.get("allowed_numbers", set())
        issues = []
        
        # Extract currency amounts
        currencies = re.findall(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', body)
        for c in currencies:
            clean_c = c.replace(",", "")
            if clean_c not in allowed_nums and str(int(float(clean_c))) not in allowed_nums:
                issues.append(f"Ungrounded currency claim: ₹{c}")

        # Extract percentages
        percentages = re.findall(r'\b(\d+(?:\.\d+)?)\s*%', body)
        for p in percentages:
            if p not in allowed_nums and f"{p}%" not in allowed_nums and str(int(float(p))) not in allowed_nums:
                issues.append(f"Ungrounded percentage claim: {p}%")

        return len(issues) == 0, issues

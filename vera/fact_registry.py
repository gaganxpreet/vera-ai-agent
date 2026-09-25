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
        Validates that numbers, currency amounts, percentages, offer names, source names
        and completion-action claims in body can all be grounded to projected context.
        Returns (is_valid: bool, issues: List[str]).
        """
        allowed_nums = facts.get("allowed_numbers", set())
        allowed_offers = facts.get("allowed_offers", set())
        allowed_sources = facts.get("allowed_sources", set())
        issues = []

        # ── 1. Currency amounts ─────────────────────────────────────────────
        currencies = re.findall(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', body)
        for c in currencies:
            clean_c = c.replace(",", "")
            if clean_c not in allowed_nums and str(int(float(clean_c))) not in allowed_nums:
                issues.append(f"Ungrounded currency claim: ₹{c}")

        # ── 2. Distances ────────────────────────────────────────────────────
        distances = re.findall(r'\b(\d+(?:\.\d+)?)\s*km\b', body, re.IGNORECASE)
        for d in distances:
            if d not in allowed_nums and str(int(float(d))) not in allowed_nums:
                issues.append(f"Ungrounded distance claim: {d}km")

        # ── 3. Clinical trial sample sizes (n=…) ───────────────────────────
        trials = re.findall(r'\bn\s*=\s*(\d+)\b', body, re.IGNORECASE)
        for t in trials:
            if t not in allowed_nums:
                issues.append(f"Ungrounded clinical trial sample size: n={t}")

        # ── 4. ISO dates ────────────────────────────────────────────────────
        dates = re.findall(r'\b(\d{4}-\d{2}-\d{2})\b', body)
        allowed_dates = facts.get("allowed_dates", set())
        for dt in dates:
            if dt.lower() not in allowed_dates:
                issues.append(f"Ungrounded specific date claim: {dt}")

        # ── 5. Offer name validation ────────────────────────────────────────
        # Patterns: "our <Offer Title>" / "activate <Offer Title>" / "@₹…" already covered by #1.
        # Check quoted-style offer names in the message against known offer titles.
        if allowed_offers:
            # Find candidate offer-like tokens: Title Case multi-word phrases preceded by
            # typical offer-intro words. We look for anything that looks like an offer name
            # that is NOT in the allowed set.
            offer_intro = re.findall(
                r'(?:offer|deal|discount|combo|plan|package|bundle|spotlight|promotion)[:\s]+([A-Z][A-Za-z0-9 &\'/-]{3,50})',
                body
            )
            for candidate in offer_intro:
                candidate_lower = candidate.strip().lower()
                if candidate_lower and not any(
                    candidate_lower in allowed_o or allowed_o in candidate_lower
                    for allowed_o in allowed_offers
                ):
                    issues.append(f"Possible ungrounded offer name: '{candidate.strip()}'")

        # ── 6. Source / journal name validation ────────────────────────────
        # Flag named sources cited with "according to", "published in", "per <Source>", etc.
        if allowed_sources:
            source_refs = re.findall(
                r'(?:according to|published in|per|from|in|study in|findings in|report by)\s+([A-Z][A-Za-z0-9 &\'.-]{2,60}?)(?:[,.\n]|$)',
                body
            )
            for src_candidate in source_refs:
                src_lower = src_candidate.strip().lower()
                if src_lower and len(src_lower) > 3 and not any(
                    src_lower in allowed_s or allowed_s in src_lower
                    for allowed_s in allowed_sources
                ):
                    issues.append(f"Possible ungrounded source citation: '{src_candidate.strip()}'")

        # ── 7. Fabricated completion / action claims ────────────────────────
        # These phrases assert that Vera has already taken an action — which is never true
        # in a proactive template message. Flag them as unverifiable.
        ACTION_CLAIM_PATTERNS = [
            r"\bI(?:'ve| have) (?:prepared|booked|scheduled|registered|activated|submitted|sent|confirmed|set up|set-up|created|drafted|built|generated)\b",
            r"\bSlots? (?:are|is) (?:available|open|ready)\b",
            r"\bAppointment (?:has been|is) (?:booked|scheduled|confirmed|created)\b",
            r"\bCampaign (?:has been|is) (?:activated|live|launched|started|running)\b",
            r"\bOffer (?:has been|is) (?:activated|live|launched|applied|set up)\b",
        ]
        for pattern in ACTION_CLAIM_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                issues.append(f"Unverifiable action claim detected (pattern: {pattern[:50]}…)")

        return len(issues) == 0, issues

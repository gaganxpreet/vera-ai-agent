import re
from typing import Dict, Any, Set, List, Tuple

def _normalize_numeric_token(value: Any) -> str:
    token = str(value).strip().replace(",", "").rstrip("%")
    try:
        number = float(token)
    except (TypeError, ValueError):
        return token
    return str(int(number)) if number.is_integer() else format(number, "g")


class FactRegistry:
    @staticmethod
    def extract_allowed_facts(projection: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts structured, type-specific verifiable facts (prices, distances, sample sizes,
        percentages, dates, names, offers, sources) to serve as ground-truth boundaries.
        """
        merchant = projection.get("merchant", {})
        category = projection.get("category", {})
        customer = projection.get("customer") or {}
        trigger = projection.get("trigger", {})
        t_payload = trigger.get("payload", {})

        allowed_numbers: Set[str] = set()
        allowed_prices: Set[str] = set()
        allowed_distances: Set[str] = set()
        allowed_sample_sizes: Set[str] = set()
        allowed_percentages: Set[str] = set()
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
        if item.get("trial_n") is not None:
            allowed_sample_sizes.add(str(item["trial_n"]))
        for field in ("title", "source", "summary"):
            value = item.get(field)
            if isinstance(value, str):
                allowed_dates.update(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', value))

        # Active & category offers & prices
        for off in merchant.get("active_offers", []) + merchant.get("offers", []):
            if off.get("title"):
                allowed_offers.add(off["title"].lower())
                # Extract prices from offer titles (e.g. ₹299 -> 299)
                for price_match in re.findall(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', off["title"]):
                    clean_p = price_match.replace(",", "")
                    allowed_prices.add(clean_p)
                    allowed_prices.add(str(int(float(clean_p))))
        for off in category.get("offer_catalog", []):
            if off.get("title"):
                allowed_offers.add(off["title"].lower())
                for price_match in re.findall(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', off["title"]):
                    clean_p = price_match.replace(",", "")
                    allowed_prices.add(clean_p)
                    allowed_prices.add(str(int(float(clean_p))))

        # Specific payload fields (distances, prices, sample sizes)
        if t_payload.get("distance_km") is not None:
            dist_str = str(t_payload["distance_km"])
            allowed_distances.add(dist_str)
            if "." in dist_str and dist_str.endswith(".0"):
                allowed_distances.add(dist_str.split(".")[0])
        if t_payload.get("renewal_amount") is not None:
            allowed_prices.add(str(t_payload["renewal_amount"]))

        # Extract dates from trigger payload (ISO dates, days, etc.)
        for k in ["deadline_iso", "last_refill", "stock_runs_out_iso", "expires_at", "window"]:
            v = t_payload.get(k)
            if v and isinstance(v, str):
                allowed_dates.add(v.lower())
                for d_match in re.findall(r'\b\d{4}-\d{2}-\d{2}\b', v):
                    allowed_dates.add(d_match)

        # General numbers and percentage deltas
        def collect_numbers(obj: Any):
            if isinstance(obj, (int, float)):
                str_val = str(obj)
                int_val = str(int(obj))
                allowed_numbers.add(str_val)
                allowed_numbers.add(int_val)
                if abs(obj) <= 1.0 and obj != 0:
                    pct_int = int(round(abs(obj) * 100))
                    allowed_percentages.add(str(pct_int))
                    allowed_percentages.add(f"{pct_int}%")
            elif isinstance(obj, str):
                for m in re.findall(r'\b\d+(?:\.\d+)?%?', obj):
                    allowed_numbers.add(m)
                    if "%" in m:
                        allowed_percentages.add(m.replace("%", ""))
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
            "allowed_prices": allowed_prices,
            "allowed_distances": allowed_distances,
            "allowed_sample_sizes": allowed_sample_sizes,
            "allowed_percentages": allowed_percentages,
            "allowed_names": allowed_names,
            "allowed_sources": allowed_sources,
            "allowed_offers": allowed_offers,
            "allowed_dates": allowed_dates,
            "category_slug": category.get("slug", "")
        }

    @staticmethod
    def verify_grounding(body: str, facts: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validates that numbers, currency amounts, percentages, distances, offer names, source names
        and completion-action claims in body can all be grounded to their exact type-specific evidence sets.
        Returns (is_valid: bool, issues: List[str]).
        """
        allowed_nums = facts.get("allowed_numbers", set())
        allowed_prices = facts.get("allowed_prices", set())
        allowed_distances = facts.get("allowed_distances", set())
        allowed_sample_sizes = facts.get("allowed_sample_sizes", set())
        allowed_percentages = facts.get("allowed_percentages", set())
        allowed_offers = facts.get("allowed_offers", set())
        allowed_sources = facts.get("allowed_sources", set())
        issues = []

        # ── 0. Percentages (must be in allowed_percentages or allowed_numbers) ─────
        pct_claims = re.findall(r'\b(\d+(?:\.\d+)?)\s*%(?!\w)', body)
        allowed_percentage_tokens = {
            _normalize_numeric_token(value)
            for value in allowed_percentages | allowed_nums
        }
        for pct in pct_claims:
            clean_pct = _normalize_numeric_token(pct)
            if clean_pct not in allowed_percentage_tokens:
                issues.append(f"Ungrounded percentage claim: {pct}%")

        # ── 1. Currency amounts (must be in allowed_prices or allowed_numbers) ─────
        currencies = re.findall(r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)', body)
        for c in currencies:
            clean_c = c.replace(",", "")
            int_c = str(int(float(clean_c)))
            if clean_c not in allowed_prices and clean_c not in allowed_nums and int_c not in allowed_prices and int_c not in allowed_nums:
                issues.append(f"Ungrounded currency claim: ₹{c}")

        # ── 2. Distances (must match explicit allowed_distances set) ────────────────
        distances = re.findall(r'\b(\d+(?:\.\d+)?)\s*km\b', body, re.IGNORECASE)
        for d in distances:
            clean_d = d.rstrip(".0") if d.endswith(".0") else d
            if allowed_distances and d not in allowed_distances and clean_d not in allowed_distances:
                issues.append(f"Ungrounded distance claim: {d}km")

        # ── 3. Clinical trial sample sizes (must match allowed_sample_sizes) ────────
        trials = re.findall(r'\bn\s*=\s*(\d+)\b', body, re.IGNORECASE)
        for t in trials:
            if allowed_sample_sizes and t not in allowed_sample_sizes:
                issues.append(f"Ungrounded clinical trial sample size: n={t}")

        # ── 4. ISO dates ────────────────────────────────────────────────────
        dates = re.findall(r'\b(\d{4}-\d{2}-\d{2})\b', body)
        allowed_dates = facts.get("allowed_dates", set())
        for dt in dates:
            if dt.lower() not in allowed_dates:
                issues.append(f"Ungrounded specific date claim: {dt}")

        # ── 5. Offer name validation ────────────────────────────────────────
        if allowed_offers:
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

        # ── 8. Unverifiable outcome promises ────────────────────────────────
        OUTCOME_PROMISE_PATTERNS = [
            r"\btop of (?:the )?(?:member |search |customer )?feeds?\b",
            r"\b#\s?1\s*(?:spot|position|ranking|result)\b",
            r"\bguarantee(?:d|s|ing)?\b",
            r"\bwill\s+(?:be seen by|double|triple|10x)\b",
            r"\bwill\s+reach\s+(?:more|a wider|a broader|at least|\d+)\b",
            r"\b(?:boost|raise|improve|increase)\s+(?:your\s+)?(?:ranking|visibility|reach)\s+(?:instantly|immediately|overnight|to the top)\b",
        ]
        for pattern in OUTCOME_PROMISE_PATTERNS:
            for match in re.finditer(pattern, body, re.IGNORECASE):
                if "guarantee" in pattern:
                    prefix = body[max(0, match.start() - 24):match.start()]
                    if re.search(
                        r"\b(?:not|never|no|cannot|can't|won't|don't|doesn't|will not|do not|does not)\s+(?:be\s+)?$",
                        prefix,
                        re.IGNORECASE
                    ):
                        continue
                issues.append(f"Unverifiable outcome promise detected (pattern: {pattern[:50]}…)")

        # ── 9. Unsupported trend/demand descriptors ─────────────────────────
        TREND_PATTERNS = [
            r"\bsurg(?:e|es|ed|ing)\b",
            r"\bbooming\b",
            r"\bskyrocketing\b",
            r"\bexploding\b",
            r"\btrending up\b",
            r"\bpicking up rapidly\b",
            r"\bin high demand right now\b",
        ]
        if any(re.search(pattern, body, re.IGNORECASE) for pattern in TREND_PATTERNS):
            grounded_percentages = allowed_percentage_tokens
            has_grounded_percentage = any(
                _normalize_numeric_token(pct) in grounded_percentages
                for pct in pct_claims
            )
            if not has_grounded_percentage:
                issues.append("Unsupported trend/demand claim without a grounded percentage")

        return len(issues) == 0, issues


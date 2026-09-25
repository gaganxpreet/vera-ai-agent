import hashlib
from typing import Dict, Any, List, Optional
from vera.models import ProactiveAction
from vera.fact_registry import FactRegistry

VALID_CTAS = {"binary_yes_no", "binary_yes_stop", "open_ended", "none"}
VALID_SEND_AS = {"vera", "merchant_on_behalf"}

class OutputValidator:
    def validate_and_repair_proactive(
        self,
        raw_output: Dict[str, Any],
        expected_send_as: str,
        expected_cta: str,
        template_name: str,
        previous_body_hashes: List[str],
        projection: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Deterministically verifies and repairs LLM proactive action output.
        Enforces schema, grounding, and anti-repetition.
        """
        output = dict(raw_output)
        
        # 1. Enforce send_as
        if output.get("send_as") not in VALID_SEND_AS:
            output["send_as"] = expected_send_as

        # 2. Enforce CTA
        if output.get("cta") not in VALID_CTAS:
            output["cta"] = expected_cta

        # 3. Ensure body is non-empty via projection-grounded fallback (no generic string fabrication)
        body = (output.get("body") or "").strip()
        if not body:
            if projection:
                from vera.llm_client import _generate_grounded_fallback
                fallback = _generate_grounded_fallback(projection)
                body = (fallback.get("body") or "").strip()
            if not body:
                return None  # Cannot construct a grounded body -> suppress outreach

        # 4. Fact Grounding Verification against input projection
        if projection:
            facts = FactRegistry.extract_allowed_facts(projection)
            is_grounded, issues = FactRegistry.verify_grounding(body, facts)
            if not is_grounded:
                # LLM output hallucinated facts -> generate grounded deterministic fallback
                from vera.llm_client import _generate_grounded_fallback
                fallback = _generate_grounded_fallback(projection)
                fallback_body = (fallback.get("body") or "").strip()
                # Crucial: Revalidate the fallback to ensure it satisfies grounding
                f_grounded, f_issues = FactRegistry.verify_grounding(fallback_body, facts)
                if f_grounded:
                    body = fallback_body
                    output["body"] = body
                    output["template_name"] = fallback.get("template_name", template_name)
                    output["template_params"] = fallback.get("template_params", [])
                    output["cta"] = fallback.get("cta", expected_cta)
                    output["send_as"] = fallback.get("send_as", expected_send_as)
                else:
                    return None  # If fallback also fails grounding, suppress rather than sending ungrounded text

        output["body"] = body

        # 5. Anti-repetition check: if body hash already sent in this thread, discard to avoid duplicate messaging
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if body_hash in previous_body_hashes:
            return None # Suppress duplicate send

        # 6. Template metadata
        if not output.get("template_name"):
            output["template_name"] = template_name
        params = output.get("template_params") or ["Merchant", "Update"]
        output["template_params"] = [str(p) if p is not None else "" for p in params]

        # 7. Rationale
        if not output.get("rationale"):
            output["rationale"] = "Grounded proactive message anchored on current trigger and merchant state."

        return output

    def validate_and_repair_reply(
        self,
        raw_output: Dict[str, Any],
        previous_body_hashes: List[str],
        projection: Optional[Dict[str, Any]] = None,
        inbound_message: str = ""
    ) -> Dict[str, Any]:
        """
        Deterministically verifies and repairs LLM reply output, including strict fact grounding verification
        and revalidation of replacement fallbacks.
        """
        output = dict(raw_output)
        action = output.get("action", "send")
        if action not in ["send", "wait", "end"]:
            action = "send"
        output["action"] = action

        if action == "send":
            body = (output.get("body") or "").strip()

            # Fact Grounding Verification against input projection
            if projection:
                facts = FactRegistry.extract_allowed_facts(projection)
                is_grounded, issues = FactRegistry.verify_grounding(body, facts) if body else (False, ["Empty body"])
                if not is_grounded or not body:
                    from vera.llm_client import _generate_grounded_fallback
                    fallback = _generate_grounded_fallback(projection, is_reply=True, inbound_msg=inbound_message)
                    fallback_body = fallback.get("body", "")
                    # Revalidate the fallback before sending
                    f_grounded, _ = FactRegistry.verify_grounding(fallback_body, facts) if fallback_body else (False, [])
                    if f_grounded:
                        body = fallback_body
                        output["action"] = fallback.get("action", "send")
                        output["cta"] = fallback.get("cta", "none")
                        output["rationale"] = f"Grounding repair applied: {fallback.get('rationale', '')}"
                    else:
                        output["action"] = "wait"
                        output["wait_seconds"] = 14400
                        output["body"] = None
                        output["cta"] = None
                        output["rationale"] = "Reply could not be grounded in facts; safely transitioned to wait."
                        return output
            elif not body:
                output["action"] = "wait"
                output["wait_seconds"] = 14400
                output["body"] = None
                output["cta"] = None
                output["rationale"] = "Empty reply body received; transitioned to wait state."
                return output

            # Anti-repetition: if exact same reply body was already sent, switch to wait to avoid repetitive looping
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body_hash in previous_body_hashes:
                output["action"] = "wait"
                output["wait_seconds"] = 14400
                output["body"] = None
                output["cta"] = None
                output["rationale"] = "Duplicate reply body detected without new context; transitioned to wait state to prevent looping."
                return output

            output["body"] = body
            cta = output.get("cta")
            if cta not in VALID_CTAS:
                output["cta"] = "binary_yes_no"
        elif action == "wait":
            if not output.get("wait_seconds"):
                output["wait_seconds"] = 14400 # 4 hours
        
        if not output.get("rationale"):
            output["rationale"] = f"Replied with action {action} to maintain thread continuity."

        return output

output_validator = OutputValidator()

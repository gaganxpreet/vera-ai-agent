import hashlib
from typing import Dict, Any, List, Optional
from vera.models import ProactiveAction

VALID_CTAS = {"binary_yes_no", "binary_yes_stop", "open_ended", "none"}
VALID_SEND_AS = {"vera", "merchant_on_behalf"}

class OutputValidator:
    def validate_and_repair_proactive(
        self,
        raw_output: Dict[str, Any],
        expected_send_as: str,
        expected_cta: str,
        template_name: str,
        previous_body_hashes: List[str]
    ) -> Dict[str, Any]:
        """
        Deterministically verifies and repairs LLM proactive action output.
        """
        output = dict(raw_output)
        
        # 1. Enforce send_as
        if output.get("send_as") not in VALID_SEND_AS:
            output["send_as"] = expected_send_as

        # 2. Enforce CTA
        if output.get("cta") not in VALID_CTAS:
            output["cta"] = expected_cta

        # 3. Ensure body is non-empty
        body = (output.get("body") or "").strip()
        if not body:
            body = "Hi there, checking in from Vera with an update on your profile performance."
        output["body"] = body

        # 4. Anti-repetition check
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if body_hash in previous_body_hashes:
            output["body"] = f"Following up on our earlier note: {body}"

        # 5. Template metadata
        if not output.get("template_name"):
            output["template_name"] = template_name
        if not output.get("template_params"):
            output["template_params"] = ["Merchant", "Update"]

        # 6. Rationale
        if not output.get("rationale"):
            output["rationale"] = "Grounded proactive message anchored on current trigger and merchant state."

        return output

    def validate_and_repair_reply(
        self,
        raw_output: Dict[str, Any],
        previous_body_hashes: List[str]
    ) -> Dict[str, Any]:
        """
        Deterministically verifies and repairs LLM reply output.
        """
        output = dict(raw_output)
        action = output.get("action", "send")
        if action not in ["send", "wait", "end"]:
            action = "send"
        output["action"] = action

        if action == "send":
            body = (output.get("body") or "").strip()
            if not body:
                body = "Understood! Proceeding with the discussed update."
            # Anti-repetition
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body_hash in previous_body_hashes:
                body = f"Following up to confirm our next step: {body}"
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

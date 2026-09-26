import pytest
from fastapi.testclient import TestClient
from vera.app import app
from vera.context_store import context_store
from vera.conversation_state import conversation_store
from vera.llm_client import LLMClient, llm_client
from vera.suppression import suppression_manager

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(llm_client, "api_key", None)
    context_store.clear()
    conversation_store.clear()
    suppression_manager.clear()
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_manager.clear()

def test_healthz_initial():
    response = client.get("/v1/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["contexts_loaded"]["category"] == 0
    assert data["contexts_loaded"]["merchant"] == 0

def test_metadata():
    response = client.get("/v1/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "team_name" in data
    assert "model" in data
    assert "version" in data

def test_context_push_versioning():
    # 1. Version 1 push
    res1 = client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists", "voice": {"tone": "clinical"}}
    })
    assert res1.status_code == 200
    assert res1.json()["accepted"] is True

    # 2. Idempotent re-push of version 1
    res2 = client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists", "voice": {"tone": "clinical"}}
    })
    assert res2.status_code == 200
    assert res2.json()["accepted"] is True

    # 3. Higher version 2 replaces
    res3 = client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 2,
        "payload": {"slug": "dentists", "voice": {"tone": "clinical_updated"}}
    })
    assert res3.status_code == 200
    assert res3.json()["accepted"] is True
    assert context_store.get("category", "dentists")["voice"]["tone"] == "clinical_updated"

    # 4. Stale version 1 rejected with 409
    res4 = client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists"}
    })
    assert res4.status_code == 409
    assert res4.json()["accepted"] is False
    assert res4.json()["reason"] == "stale_version"
    assert res4.json()["current_version"] == 2

def test_tick_and_suppression():
    # Load category, merchant, and trigger
    client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {
            "slug": "dentists",
            "voice": {"tone": "peer_clinical"},
            "digest": [{
                "id": "d_fluoride",
                "title": "Fluoride recall cuts caries 38%",
                "source": "JIDA Oct 2026",
                "trial_n": 2100
            }]
        }
    })
    client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": "m_meera",
        "version": 1,
        "payload": {
            "merchant_id": "m_meera",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Meera's Clinic", "owner_first_name": "Meera"}
        }
    })
    client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": "trg_01",
        "version": 1,
        "payload": {
            "id": "trg_01",
            "scope": "merchant",
            "kind": "research_digest",
            "merchant_id": "m_meera",
            "payload": {"category": "dentists", "top_item_id": "d_fluoride"},
            "suppression_key": "suppress:trg_01",
            "urgency": 4
        }
    })

    # First tick -> produces 1 action
    tick1 = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_01"]
    })
    assert tick1.status_code == 200
    actions = tick1.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["merchant_id"] == "m_meera"
    assert "JIDA" in actions[0]["body"]
    assert actions[0]["template_name"] is not None

    # Second tick with same trigger -> suppressed (returns 0 actions)
    tick2 = client.post("/v1/tick", json={
        "now": "2026-04-26T10:05:00Z",
        "available_triggers": ["trg_01"]
    })
    assert tick2.status_code == 200
    assert len(tick2.json()["actions"]) == 0

def test_tick_composes_all_eligible_triggers():
    client.post("/v1/context", json={
        "scope": "category", "context_id": "salons", "version": 1,
        "payload": {"slug": "salons"}
    })
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_multi", "version": 1,
        "payload": {"merchant_id": "m_multi", "name": "Studio Multi", "category_slug": "salons"}
    })
    trigger_ids = [f"trg_multi_{index}" for index in range(1, 22)]
    for trigger_id in trigger_ids:
        client.post("/v1/context", json={
            "scope": "trigger", "context_id": trigger_id, "version": 1,
            "payload": {
                "id": trigger_id, "kind": "curious_ask_due", "scope": "merchant",
                "merchant_id": "m_multi", "payload": {}
            }
        })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z", "available_triggers": trigger_ids
    })

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 20
    assert {action["trigger_id"] for action in actions} == set(trigger_ids[:20])

@pytest.mark.asyncio
async def test_gemini_retries_once_on_transient_server_error(monkeypatch):
    import httpx

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.payload = payload or {}

        def json(self):
            return self.payload

    class FakeAsyncClient:
        requests = 0

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, **kwargs):
            type(self).requests += 1
            if type(self).requests == 1:
                return FakeResponse(503)
            return FakeResponse(200, {
                "candidates": [{"content": {"parts": [{"text": '{"body":"ready"}'}]}}]
            })

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LLMClient()
    client.provider = "gemini"
    client.api_key = "test-key"
    client.gemini_models = ["gemini-primary", "gemini-secondary"]

    result = await client.acomplete("system", "user")

    assert result == '{"body":"ready"}'
    assert FakeAsyncClient.requests == 2

@pytest.mark.asyncio
async def test_gemini_rotates_model_on_quota_exhaustion(monkeypatch):
    import httpx

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.payload = payload or {}

        def json(self):
            return self.payload

    class FakeAsyncClient:
        urls = []

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, **kwargs):
            type(self).urls.append(url)
            if len(type(self).urls) == 1:
                return FakeResponse(429)
            return FakeResponse(200, {
                "candidates": [{"content": {"parts": [{"text": '{"body":"ready"}'}]}}]
            })

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LLMClient()
    client.provider = "gemini"
    client.api_key = "test-key"
    client.gemini_models = ["gemini-primary", "gemini-secondary"]

    result = await client.acomplete("system", "user")

    assert result == '{"body":"ready"}'
    assert len(FakeAsyncClient.urls) == 2
    assert "/models/gemini-primary:" in FakeAsyncClient.urls[0]
    assert "/models/gemini-secondary:" in FakeAsyncClient.urls[1]

def test_reply_intent_transition_and_hostile():
    # Acceptance transition to ACTION mode
    rep1 = client.post("/v1/reply", json={
        "conversation_id": "conv_test_1",
        "merchant_id": "m_meera",
        "message": "Ok let's do it. What's next?",
        "turn_number": 2
    })
    assert rep1.status_code == 200
    assert rep1.json()["action"] == "send"
    assert any(w in rep1.json()["body"].lower() for w in ["noted", "confirm", "details", "proceed", "great", "plan"])


    # Opt-out / Hostile termination
    rep2 = client.post("/v1/reply", json={
        "conversation_id": "conv_test_2",
        "merchant_id": "m_meera",
        "message": "Stop messaging me. This is spam.",
        "turn_number": 2
    })
    assert rep2.status_code == 200
    assert rep2.json()["action"] == "end"
    assert "opted out" in rep2.json()["rationale"].lower()

def test_auto_reply_detection():
    auto_msg = "Thank you for contacting Dr. Meera! Our team will respond shortly."
    # Turn 1 of auto-reply -> WAIT
    r1 = client.post("/v1/reply", json={
        "conversation_id": "conv_auto",
        "merchant_id": "m_meera",
        "message": auto_msg,
        "turn_number": 2
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "wait"
    assert r1.json()["wait_seconds"] > 0

    # Turn 2 of repeated auto-reply -> END
    r2 = client.post("/v1/reply", json={
        "conversation_id": "conv_auto",
        "merchant_id": "m_meera",
        "message": auto_msg,
        "turn_number": 3
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "end"

def test_expired_trigger_ignored():
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_exp", "version": 1,
        "payload": {"name": "Test Merchant", "category_slug": "salons"}
    })
    client.post("/v1/context", json={
        "scope": "category", "context_id": "salons", "version": 1,
        "payload": {"slug": "salons"}
    })
    # Trigger expired at 18:00
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_exp", "version": 1,
        "payload": {
            "kind": "curious_ask_due",
            "merchant_id": "m_exp",
            "expires_at": "2026-09-25T18:00:00Z"
        }
    })
    # Simulation now is 20:00 (after expiry)
    tick_resp = client.post("/v1/tick", json={
        "now": "2026-09-25T20:00:00Z",
        "available_triggers": ["trg_exp"]
    })
    assert tick_resp.status_code == 200
    assert len(tick_resp.json()["actions"]) == 0

def test_category_fit_no_tooth_emoji_for_gym():
    from bot import compose
    gym_category = {"slug": "gyms", "display_name": "Gyms & Fitness"}
    gym_merchant = {"name": "Zen Yoga Studio", "category_slug": "gyms"}
    gym_customer = {"name": "Diya"}
    recall_trigger = {
        "id": "trg_gym_recall",
        "kind": "recall_due",
        "scope": "customer",
        "merchant_id": "m_zen",
        "customer_id": "c_diya",
        "payload": {"metric_or_topic": "recall_due"}
    }
    res = compose(gym_category, gym_merchant, recall_trigger, gym_customer)
    body = res["body"]
    assert "🦷" not in body
    assert "cleaning" not in body.lower()
    assert "dental" not in body.lower()
    assert any(term in body.lower() for term in ["fitness", "workout", "session", "progress"])

def test_hard_opt_out_on_subsequent_reply():
    # 1. Opt out on turn 2
    r1 = client.post("/v1/reply", json={
        "conversation_id": "conv_optout",
        "merchant_id": "m_opt",
        "message": "stop unsubscribe",
        "turn_number": 2
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "end"

    # 2. Subsequent normal message on same conversation should immediately end without LLM
    r2 = client.post("/v1/reply", json={
        "conversation_id": "conv_optout",
        "merchant_id": "m_opt",
        "message": "Hello are you there? I want to start",
        "turn_number": 3
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "end"

def test_fact_grounding_validator():
    from vera.fact_registry import FactRegistry
    projection = {
        "merchant": {"name": "Apollo Pharmacy", "locality": "Jaipur"},
        "trigger": {"payload": {"delta_pct": -0.20, "days_remaining": 7}}
    }
    facts = FactRegistry.extract_allowed_facts(projection)
    
    # Grounded claim with verified facts
    valid, issues = FactRegistry.verify_grounding("Noticed a 20% dip over 7 days in Jaipur", facts)
    assert valid is True

    # Hallucinated number
    valid, issues = FactRegistry.verify_grounding("Your revenue dropped by 88% and you owe ₹99999", facts)
    assert valid is False
    assert len(issues) >= 1

def test_consent_revoked_trigger_blocked():
    """Trigger whose customer has revoked consent must produce zero actions."""
    client.post('/v1/teardown')
    client.post('/v1/context', json={
        'scope': 'customer', 'context_id': 'c_revoked', 'version': 1,
        'payload': {'name': 'Rahul', 'consent': {'status': 'revoked', 'scope': ['whatsapp']}}
    })
    client.post('/v1/context', json={
        'scope': 'merchant', 'context_id': 'm_rev_test', 'version': 1,
        'payload': {'name': 'Test Merchant', 'category_slug': 'gyms'}
    })
    client.post('/v1/context', json={
        'scope': 'trigger', 'context_id': 'trg_revoked_consent', 'version': 1,
        'payload': {
            'kind': 'perf_dip',
            'merchant_id': 'm_rev_test',
            'customer_id': 'c_revoked',
            'payload': {'delta_pct': -0.30}
        }
    })
    tick_resp = client.post('/v1/tick', json={
        'now': '2026-09-26T10:00:00Z',
        'available_triggers': ['trg_revoked_consent']
    })
    assert tick_resp.status_code == 200
    assert len(tick_resp.json()['actions']) == 0, \
        'Trigger linked to revoked-consent customer must be blocked by the router'


def test_stale_context_version_returns_409():
    """Pushing a context with version <= current must return 409 Conflict."""
    r1 = client.post('/v1/context', json={
        'scope': 'merchant', 'context_id': 'm_versioned', 'version': 5,
        'payload': {'name': 'Version Test Merchant', 'category_slug': 'restaurants'}
    })
    assert r1.status_code == 200

    r2 = client.post('/v1/context', json={
        'scope': 'merchant', 'context_id': 'm_versioned', 'version': 4,
        'payload': {'name': 'Version Test Merchant stale'}
    })
    assert r2.status_code == 409
    body = r2.json()
    assert body['accepted'] is False
    assert body['reason'] == 'stale_version'

    r3 = client.post('/v1/context', json={
        'scope': 'merchant', 'context_id': 'm_versioned', 'version': 6,
        'payload': {'name': 'Version Test Merchant fresh', 'category_slug': 'restaurants'}
    })
    assert r3.status_code == 200
    assert r3.json()['accepted'] is True


def test_last_trigger_id_preserved_on_reply():
    """After a proactive message, replies must preserve the original trigger context."""
    from vera.conversation_state import conversation_store

    conv_id = 'conv_trigger_preserve'
    conv = conversation_store.get_or_create(conv_id, merchant_id='m_001', customer_id=None)
    conv.last_trigger_id = 'trg_001_perf_dip'
    conv.status = 'PROACTIVE_SENT'

    resp = client.post('/v1/reply', json={
        'conversation_id': conv_id,
        'merchant_id': 'm_001',
        'message': 'What exactly dropped? Tell me more.',
        'turn_number': 2
    })
    assert resp.status_code == 200
    assert resp.json()['action'] in ('send', 'wait', 'end')

    conv_after = conversation_store.get_or_create(conv_id, merchant_id='m_001', customer_id=None)
    assert conv_after.last_trigger_id == 'trg_001_perf_dip',         'last_trigger_id was wiped during reply - trigger context lost'

def test_cross_conversation_auto_reply_isolation():
    """Auto-reply in Conv A should not cause Conv B for the same merchant to immediately terminate."""
    auto_msg = "Thank you for reaching out to us! Our team will respond shortly."
    
    # Conv A - Turn 1 -> WAIT
    r_a1 = client.post("/v1/reply", json={
        "conversation_id": "conv_iso_A",
        "merchant_id": "m_iso_merchant",
        "message": auto_msg,
        "turn_number": 2
    })
    assert r_a1.status_code == 200
    assert r_a1.json()["action"] == "wait"

    # Conv B - Turn 1 -> should ALSO be WAIT (not prematurely terminated because of A)
    r_b1 = client.post("/v1/reply", json={
        "conversation_id": "conv_iso_B",
        "merchant_id": "m_iso_merchant",
        "message": auto_msg,
        "turn_number": 2
    })
    assert r_b1.status_code == 200
    assert r_b1.json()["action"] == "wait"


def test_e2e_reply_uses_preserved_perf_metric():
    """End-to-end: query on a perf_dip conversation returns the exact percentage drop from trigger."""
    # Push context for merchant and perf trigger
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_perf_query", "version": 1,
        "payload": {
            "merchant_id": "m_perf_query",
            "category_slug": "restaurants",
            "identity": {"name": "Biryani Express", "owner_first_name": "Tariq"}
        }
    })
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_perf_drop_35", "version": 1,
        "payload": {
            "id": "trg_perf_drop_35",
            "scope": "merchant",
            "kind": "perf_dip",
            "merchant_id": "m_perf_query",
            "payload": {"metric": "views", "delta_pct": -0.35, "window": "7d"},
            "urgency": 4
        }
    })

    # Trigger proactive tick
    t_resp = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_perf_drop_35"]
    })
    assert t_resp.status_code == 200
    actions = t_resp.json()["actions"]
    assert len(actions) == 1
    conv_id = actions[0]["conversation_id"]

    # Merchant asks clarifying question
    r_resp = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_perf_query",
        "message": "What metric dropped and by how much?",
        "turn_number": 2
    })
    assert r_resp.status_code == 200
    reply_body = r_resp.json()["body"]
    # Body must ground on the original trigger's 35% drop
    assert "35%" in reply_body
    assert "views" in reply_body.lower() or "biryani express" in reply_body.lower()


def test_production_composer_rejects_hallucinated_facts():
    """Output validator repairs/replaces hallucinated currency and % in production composition."""
    from vera.validator import output_validator
    
    projection = {
        "merchant": {"name": "Dental Care Plus", "locality": "Indiranagar", "owner_first_name": "Rohan"},
        "category": {"slug": "dentists", "voice": {"tone": "clinical"}},
        "trigger": {"kind": "perf_dip", "payload": {"metric": "calls", "delta_pct": -0.25}}
    }
    
    # Raw output hallucinating non-existent 85% and ₹50000
    hallucinated_raw = {
        "body": "Hi Rohan, revenue crashed by 85% and you lost ₹50,000 this week. Want a fix?",
        "cta": "binary_yes_no",
        "send_as": "vera",
        "template_name": "vera_perf_dip_v1",
        "template_params": ["Rohan", "calls", "85%"],
        "rationale": "Hallucinated pitch"
    }

    validated = output_validator.validate_and_repair_proactive(
        hallucinated_raw,
        expected_send_as="vera",
        expected_cta="binary_yes_no",
        template_name="vera_perf_dip_v1",
        previous_body_hashes=[],
        projection=projection
    )

    # Grounded fallback must replace hallucinated numbers with verified facts
    assert "₹50,000" not in validated["body"]
    assert "85%" not in validated["body"]
    assert "25%" in validated["body"]


def test_reply_prompt_includes_context_projection():
    """Verify build_reply_prompt incorporates context projection facts into user prompt."""
    from vera.prompt_builder import build_reply_prompt

    projection = {
        "merchant": {"name": "Biryani House", "owner_first_name": "Karan"},
        "trigger": {"kind": "perf_dip", "payload": {"metric": "leads", "delta_pct": -0.40}}
    }

    prompts = build_reply_prompt(
        inbound_message="What dropped exactly?",
        intent="QUESTION",
        mode="EXPLORATION",
        projection=projection,
        recent_turns=[]
    )

    assert "Context Projection" in prompts["user"]
    assert "leads" in prompts["user"]
    assert "40%" in prompts["user"] or "-0.4" in prompts["user"]

def test_reply_uses_merchant_context_and_hinglish_prompt():
    from vera.context_selector import project_context_for_trigger
    from vera.llm_client import _generate_grounded_fallback
    from vera.prompt_builder import build_composition_prompt, build_reply_prompt
    from vera.strategies import STRATEGY_REGISTRY
    from vera.validator import output_validator

    merchant = {
        "merchant_id": "m_meera", "category_slug": "dentists",
        "identity": {
            "name": "Dr. Meera's Clinic", "owner_first_name": "Meera",
            "locality": "Lajpat Nagar", "languages": ["en", "hi"]
        },
        "performance": {"ctr": 0.021},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
        "signals": ["high_risk_adult_cohort"],
        "conversation_history": [{"engagement": "merchant_replied"}]
    }
    category = {
        "slug": "dentists", "voice": {"tone": "peer_clinical"},
        "peer_stats": {"avg_ctr": 0.030},
        "peer_campaigns": [{"count": 3, "category": "dentists", "locality": "Lajpat Nagar", "type": "recall", "period": "this month"}],
        "digest": [{"id": "d_jida", "title": "Fluoride recall findings", "source": "JIDA Oct 2026"}]
    }
    trigger = {
        "id": "trg_research", "kind": "research_digest", "scope": "merchant",
        "payload": {"top_item_id": "d_jida", "category": "dentists"}
    }
    projection = project_context_for_trigger(
        category, merchant, trigger, include_reply_context=True
    )

    composition = build_composition_prompt(
        projection, STRATEGY_REGISTRY["research_digest"]
    )
    reply = build_reply_prompt(
        "Yes, please", "ACCEPTANCE", "ACTION", projection, [],
        previous_vera_message="I can share the JIDA summary."
    )
    fallback = _generate_grounded_fallback(
        projection, is_reply=True, inbound_msg="Yes, please", intent="ACCEPTANCE"
    )

    assert projection["merchant"]["performance"]["ctr"] == 0.021
    assert projection["merchant"]["active_offers"][0]["title"] == "Dental Cleaning @ ₹299"
    assert "mix Hindi and English" in composition["system"]
    assert "3 dentists in Lajpat Nagar" in composition["system"]
    assert '"count": 3' in composition["user"]
    assert "peer_clinical" in reply["system"]
    assert "I can share the JIDA summary." in reply["user"]
    assert "JIDA Oct 2026" in fallback["body"]
    assert "CTR 2.1% below peer median 3.0%" in fallback["rationale"]
    assert "high-risk adult cohort" in fallback["rationale"]

    validated = output_validator.validate_and_repair_reply(
        {
            "action": "send", "body": "I'll prepare the requested summary.",
            "cta": "none", "rationale": "Merchant accepted/confirmed as per ACCEPTANCE rules"
        },
        previous_body_hashes=[], projection=projection, inbound_message="Yes, please"
    )
    assert "CTR 2.1% below peer median 3.0%" in validated["rationale"]


def test_semantic_type_grounding_rejects_mismatched_distance_claim():
    """Validator rejects distance claim (720km) even if 720 exists as a view count in peer stats."""
    from vera.validator import output_validator

    projection = {
        "merchant": {"name": "Dental Hub", "locality": "Lajpat Nagar", "owner_first_name": "Dr. Sameer"},
        "category": {"slug": "dentists", "peer_stats": {"avg_views_30d": 720}},
        "trigger": {"kind": "competitor_opened", "payload": {"competitor": "Smile Clinic", "distance_km": 1.3}}
    }

    # Raw LLM output misusing 720 views as a 720km distance claim
    mismatched_raw = {
        "body": "Hi Dr. Sameer, a competitor opened 720km away in Lajpat Nagar. Want to see recommendations?",
        "cta": "binary_yes_no",
        "send_as": "vera",
        "template_name": "vera_competitor_alert_v1",
        "template_params": ["Dr. Sameer", "720km"],
        "rationale": "Mismatched distance claim"
    }

    validated = output_validator.validate_and_repair_proactive(
        mismatched_raw,
        expected_send_as="vera",
        expected_cta="binary_yes_no",
        template_name="vera_competitor_alert_v1",
        previous_body_hashes=[],
        projection=projection
    )

    # 720km distance claim must be caught and replaced with grounded distance (1.3km)
    assert "720km" not in validated["body"]
    assert "1.3km" in validated["body"]


def test_grounding_rejects_unsupported_source_and_action_claims():
    """Validator rejects ungrounded source citations and fabricated pre-action claims."""
    from vera.validator import output_validator

    projection = {
        "merchant": {"name": "Lajpat Dental", "owner_first_name": "Dr. Ankit"},
        "category": {"slug": "dentists"},
        "trigger": {"kind": "research_digest", "payload": {}}
    }

    # Raw LLM output with ungrounded journal source and fabricated action claim
    hallucinated = {
        "body": "Dr. Ankit, according to Harvard Medical Journal, I've booked your patient appointment. Want to see?",
        "cta": "binary_yes_no",
        "send_as": "vera",
        "template_name": "vera_research_digest_v1",
        "template_params": ["Dr. Ankit"],
        "rationale": "Hallucinated source and action claim"
    }

    validated = output_validator.validate_and_repair_proactive(
        hallucinated,
        expected_send_as="vera",
        expected_cta="binary_yes_no",
        template_name="vera_research_digest_v1",
        previous_body_hashes=[],
        projection=projection
    )

    assert "Harvard Medical Journal" not in validated["body"]
    assert "I've booked" not in validated["body"]


def test_empty_output_falls_back_without_generic_fabrication():
    """Empty LLM output is grounded against projection facts, not generic hardcoded strings."""
    from vera.validator import output_validator

    projection = {
        "merchant": {"name": "Sunrise Pharmacy", "locality": "Gomti Nagar", "owner_first_name": "Vikas"},
        "trigger": {"kind": "perf_dip", "payload": {"metric": "views", "delta_pct": -0.30}}
    }

    # Empty raw LLM output
    empty_raw = {"body": "", "cta": "binary_yes_no"}

    validated = output_validator.validate_and_repair_proactive(
        empty_raw,
        expected_send_as="vera",
        expected_cta="binary_yes_no",
        template_name="vera_perf_dip_v1",
        previous_body_hashes=[],
        projection=projection
    )

    # Must be grounded on the 30% views drop, not generic profile check-in string
    assert validated is not None
    assert "30%" in validated["body"]
    assert "views" in validated["body"]






import pytest
from fastapi.testclient import TestClient
from vera.app import app
from vera.context_store import context_store
from vera.conversation_state import conversation_store
from vera.suppression import suppression_manager

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_state():
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
    assert any(w in rep1.json()["body"].lower() for w in ["done", "set", "proceed", "schedule", "motion"])

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


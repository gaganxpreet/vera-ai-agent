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

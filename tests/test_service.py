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

def test_root_health_route():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

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

def test_distant_festival_uses_early_planning_strategy_and_fallback():
    from vera.llm_client import _generate_grounded_fallback
    from vera.strategies import get_strategy_for_kind

    far_trigger = {
        "id": "trg_far_diwali", "kind": "festival_upcoming", "scope": "merchant",
        "urgency": 1, "payload": {"festival": "Diwali", "days_until": 188}
    }
    near_trigger = {
        "id": "trg_near_diwali", "kind": "festival_upcoming", "scope": "merchant",
        "urgency": 4, "payload": {"festival": "Diwali", "days_until": 8}
    }
    merchant = {"name": "Studio Eleven", "owner_first_name": "Riya", "locality": "Banjara Hills"}
    far_strategy = get_strategy_for_kind("festival_upcoming", trigger=far_trigger)
    near_strategy = get_strategy_for_kind("festival_upcoming", trigger=near_trigger)
    fallback = _generate_grounded_fallback({
        "trigger": far_trigger,
        "merchant": merchant,
        "category": {"slug": "salons"}
    })

    assert far_strategy.cta_type == "open_ended"
    assert "early festival planning" in far_strategy.primary_goal
    assert near_strategy.cta_type == "binary_yes_no"
    assert fallback["cta"] == "open_ended"
    assert "188 days away" in fallback["body"]
    assert "Which service" in fallback["body"]
    assert "draft a" not in fallback["body"].lower()
    assert "demand" not in fallback["body"].lower()

    missing_event_strategy = get_strategy_for_kind(
        "festival_upcoming",
        trigger={"kind": "festival_upcoming", "scope": "merchant", "payload": {"metric_or_topic": "festival_upcoming"}}
    )
    missing_event_fallback = _generate_grounded_fallback({
        "trigger": {"kind": "festival_upcoming", "payload": {}},
        "merchant": merchant,
        "category": {"slug": "salons"}
    })
    assert missing_event_strategy.cta_type == "open_ended"
    assert missing_event_fallback["cta"] == "open_ended"
    assert "festival name or date" in missing_event_fallback["body"]
    assert "demand" not in missing_event_fallback["body"].lower()

def test_distant_festival_is_deprioritized_against_urgent_trigger():
    client.post("/v1/context", json={
        "scope": "category", "context_id": "salons", "version": 1,
        "payload": {"slug": "salons"}
    })
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_festival_rank", "version": 1,
        "payload": {"merchant_id": "m_festival_rank", "name": "Studio Eleven", "category_slug": "salons"}
    })
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_far_rank", "version": 1,
        "payload": {
            "id": "trg_far_rank", "kind": "festival_upcoming", "scope": "merchant",
            "merchant_id": "m_festival_rank", "urgency": 1,
            "expires_at": "2026-12-01T00:00:00Z",
            "payload": {"festival": "Diwali", "days_until": 188}
        }
    })
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_urgent_rank", "version": 1,
        "payload": {
            "id": "trg_urgent_rank", "kind": "regulation_change", "scope": "merchant",
            "merchant_id": "m_festival_rank", "urgency": 4,
            "payload": {"deadline_iso": "2026-05-01"}
        }
    })

    from vera.trigger_router import trigger_router
    ranked = trigger_router.evaluate_triggers(
        ["trg_far_rank", "trg_urgent_rank"], now="2026-04-29T10:00:00Z"
    )

    assert [item["trigger_id"] for item in ranked] == ["trg_urgent_rank", "trg_far_rank"]

def test_distant_festival_tick_uses_early_planning_message():
    client.post("/v1/context", json={
        "scope": "category", "context_id": "salons", "version": 1,
        "payload": {"slug": "salons", "voice": {"tone": "warm_practical"}}
    })
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_far_festival", "version": 1,
        "payload": {
            "merchant_id": "m_far_festival", "category_slug": "salons",
            "identity": {"name": "Studio Eleven", "owner_first_name": "Riya", "locality": "Banjara Hills"}
        }
    })
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_far_festival", "version": 1,
        "payload": {
            "id": "trg_far_festival", "kind": "festival_upcoming", "scope": "merchant",
            "merchant_id": "m_far_festival", "urgency": 1,
            "expires_at": "2026-12-01T00:00:00Z",
            "payload": {"festival": "Diwali", "days_until": 188}
        }
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_far_festival"]
    })

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["cta"] == "open_ended"
    assert "188 days away" in actions[0]["body"]
    assert "which service" in actions[0]["body"].lower()
    assert "draft a" not in actions[0]["body"].lower()
    assert "demand" not in actions[0]["body"].lower()

def test_festival_placeholder_tick_asks_for_missing_event():
    client.post("/v1/context", json={
        "scope": "category", "context_id": "gyms", "version": 1,
        "payload": {"slug": "gyms"}
    })
    client.post("/v1/context", json={
        "scope": "merchant", "context_id": "m_festival_placeholder", "version": 1,
        "payload": {
            "merchant_id": "m_festival_placeholder", "category_slug": "gyms",
            "identity": {"name": "Bend & Burn", "owner_first_name": "Pooja", "locality": "Koramangala"}
        }
    })
    client.post("/v1/context", json={
        "scope": "trigger", "context_id": "trg_festival_placeholder", "version": 1,
        "payload": {
            "id": "trg_festival_placeholder", "kind": "festival_upcoming", "scope": "merchant",
            "merchant_id": "m_festival_placeholder", "urgency": 1,
            "expires_at": "2026-12-01T00:00:00Z",
            "payload": {"placeholder": True, "metric_or_topic": "festival_upcoming"}
        }
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_festival_placeholder"]
    })

    assert response.status_code == 200
    action = response.json()["actions"][0]
    assert action["cta"] == "open_ended"
    assert "Bend & Burn" in action["body"]
    assert "Koramangala" in action["body"]
    assert "gym campaign idea" in action["body"]
    assert "demand" not in action["body"].lower()
    assert "draft" not in action["body"].lower()

def test_ipl_match_today_uses_explicit_payload_allowlist():
    from vera.context_selector import project_context_for_trigger

    projection = project_context_for_trigger(
        {"slug": "restaurants"},
        {"merchant_id": "m_pizza", "category_slug": "restaurants"},
        {
            "id": "trg_ipl_today", "kind": "ipl_match_today", "scope": "merchant",
            "payload": {
                "match": "DC vs MI", "venue": "Arun Jaitley Stadium", "city": "Delhi",
                "match_time_iso": "2026-04-26T19:30:00+05:30", "is_weeknight": False,
                "placeholder": "discard me"
            }
        }
    )

    assert projection["trigger"]["payload"] == {
        "match": "DC vs MI", "venue": "Arun Jaitley Stadium", "city": "Delhi",
        "match_time_iso": "2026-04-26T19:30:00+05:30", "is_weeknight": False
    }

@pytest.mark.asyncio
async def test_gemini_retries_once_on_transient_server_error(monkeypatch):
    import asyncio
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
    sleep_calls = []
    async def fake_sleep(delay):
        sleep_calls.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("vera.llm_client._backoff_with_jitter", lambda attempt: 0.2)
    client = LLMClient()
    client.provider = "gemini"
    client.api_key = "test-key"
    client.gemini_models = ["gemini-primary", "gemini-secondary"]

    result = await client.acomplete("system", "user")

    assert result == '{"body":"ready"}'
    assert FakeAsyncClient.requests == 2
    assert sleep_calls == [0.2]

@pytest.mark.asyncio
async def test_gemini_rotates_model_on_quota_exhaustion(monkeypatch):
    import asyncio
    import httpx

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.payload = payload or {}
            self.headers = {"Retry-After": "0.75"} if status_code == 429 else {}

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
    sleep_calls = []
    async def fake_sleep(delay):
        sleep_calls.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = LLMClient()
    client.provider = "gemini"
    client.api_key = "test-key"
    client.gemini_models = ["gemini-primary", "gemini-secondary"]

    result = await client.acomplete("system", "user")

    assert result == '{"body":"ready"}'
    assert len(FakeAsyncClient.urls) == 2
    assert "/models/gemini-primary:" in FakeAsyncClient.urls[0]
    assert "/models/gemini-secondary:" in FakeAsyncClient.urls[1]
    assert sleep_calls == [0.75]

def test_retry_after_parser_handles_missing_and_invalid_headers():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    from vera.llm_client import _parse_retry_after

    class ResponseWithoutHeaders:
        pass

    class ResponseWithHeaders:
        headers = {"Retry-After": "not-a-delay"}

    class ResponseWithHttpDate:
        headers = {
            "Retry-After": format_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True
            )
        }

    assert _parse_retry_after(ResponseWithoutHeaders(), 2.0) == 0.0
    assert _parse_retry_after(ResponseWithHeaders(), 2.0) == 0.0
    assert _parse_retry_after(ResponseWithHeaders(), -1.0) == 0.0
    assert _parse_retry_after(ResponseWithHttpDate(), 0.5) == 0.5

def test_judge_simulator_gemini_rotates_on_quota_exhaustion(monkeypatch):
    from email.message import Message
    import json
    import judge_simulator

    requests = []
    sleeps = []

    class FakeResponse:
        def read(self):
            return json.dumps({
                "candidates": [{"content": {"parts": [{"text": "ready"}]}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request.full_url)
        if len(requests) == 1:
            headers = Message()
            headers["Retry-After"] = "0.75"
            raise judge_simulator.urlerror.HTTPError(
                request.full_url, 429, "Too Many Requests", headers, None
            )
        return FakeResponse()

    monkeypatch.setattr(judge_simulator.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(judge_simulator.time, "sleep", sleeps.append)
    provider = judge_simulator.GeminiProvider(
        "test-key", "gemini-primary", fallback_models=["gemini-secondary"]
    )

    response = provider.complete("Say ready")

    assert response == "ready"
    assert len(requests) == 2
    assert "/models/gemini-primary:" in requests[0]
    assert "/models/gemini-secondary:" in requests[1]
    assert sleeps == [0.75]

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

def test_fact_grounding_allows_dates_in_category_digest_text():
    from vera.fact_registry import FactRegistry

    projection = {
        "category": {
            "target_digest_item": {
                "title": "DCI circular dated 2026-11-04",
                "source": "DCI Oct 2026",
                "summary": "New dose limits effective 2026-12-01"
            }
        },
        "trigger": {"payload": {}}
    }
    facts = FactRegistry.extract_allowed_facts(projection)

    valid, issues = FactRegistry.verify_grounding(
        "The DCI circular dated 2026-11-04 sets guidance effective 2026-12-01.",
        facts
    )

    assert valid is True
    assert issues == []

def test_fact_grounding_rejects_unverifiable_outcome_promise():
    from vera.fact_registry import FactRegistry

    facts = FactRegistry.extract_allowed_facts({
        "merchant": {"name": "Zen Yoga Studio"},
        "trigger": {"payload": {"delta_pct": 0.31}}
    })
    body = "Calls surged 31% this week, putting you back on top of member feeds."

    valid, issues = FactRegistry.verify_grounding(body, facts)

    assert valid is False
    assert any("outcome promise" in issue.lower() for issue in issues)

def test_fact_grounding_requires_a_grounded_stat_for_trend_claim():
    from vera.fact_registry import FactRegistry

    empty_facts = FactRegistry.extract_allowed_facts({
        "merchant": {}, "category": {}, "trigger": {"payload": {}}
    })
    valid, issues = FactRegistry.verify_grounding(
        "Local demand is surging ahead of Diwali.", empty_facts
    )
    assert valid is False
    assert any("trend/demand" in issue.lower() for issue in issues)

    unrelated_number_facts = FactRegistry.extract_allowed_facts({
        "merchant": {}, "category": {}, "trigger": {"payload": {"days_until": 8}}
    })
    valid, issues = FactRegistry.verify_grounding(
        "Local demand is surging in 2026.", unrelated_number_facts
    )
    assert valid is False
    assert any("trend/demand" in issue.lower() for issue in issues)

    grounded_facts = FactRegistry.extract_allowed_facts({
        "merchant": {}, "category": {}, "trigger": {"payload": {"delta_pct": 0.42}}
    })
    valid, issues = FactRegistry.verify_grounding(
        "Local searches are surging, up 42% this week.", grounded_facts
    )
    assert valid is True
    assert issues == []

    decimal_facts = FactRegistry.extract_allowed_facts({
        "merchant": {}, "category": {}, "trigger": {"payload": {"delta_pct": 0.20}}
    })
    valid, issues = FactRegistry.verify_grounding(
        "Local searches are surging, up 20.0% this week.", decimal_facts
    )
    assert valid is True
    assert issues == []

def test_outcome_grounding_does_not_flag_reach_out_language():
    from vera.fact_registry import FactRegistry

    facts = FactRegistry.extract_allowed_facts({"merchant": {}, "trigger": {"payload": {}}})
    valid, issues = FactRegistry.verify_grounding("I'll reach out to three partners tomorrow.", facts)

    assert valid is True
    assert issues == []

    disclaimer_valid, disclaimer_issues = FactRegistry.verify_grounding(
        "We can't guarantee results, but I'll reach out with verified details.", facts
    )
    assert disclaimer_valid is True
    assert disclaimer_issues == []

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
    assert "NEVER claim an action was started, booked, sent, queued, approved" in reply["system"]
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


def test_dentist_dr_prefix_across_merchant_facing_fallback_branches():
    """Merchant-facing dentist fallbacks address the owner as 'Dr. {name}' (category voice / case studies)."""
    from vera.llm_client import _generate_grounded_fallback

    dentist_merchant = {
        "name": "Dr. Meera's Clinic", "owner_first_name": "Meera",
        "locality": "Lajpat Nagar", "category_slug": "dentists"
    }

    # perf_dip is a plain merchant-facing branch that previously said "Hi Meera"
    perf = _generate_grounded_fallback({
        "trigger": {"kind": "perf_dip", "payload": {"metric": "calls", "delta_pct": -0.25}},
        "merchant": dentist_merchant,
        "category": {"slug": "dentists"}
    })
    assert perf["body"].startswith("Hi Dr. Meera,")

    # competitor is another merchant-facing branch
    comp = _generate_grounded_fallback({
        "trigger": {"kind": "competitor_opened", "payload": {"distance_km": 1.3}},
        "merchant": dentist_merchant,
        "category": {"slug": "dentists"}
    })
    assert "Dr. Meera" in comp["body"]

    # research_digest already hardcoded "Dr." — must NOT double-prefix to "Dr. Dr. Meera"
    digest = _generate_grounded_fallback({
        "trigger": {"kind": "research_digest", "payload": {}},
        "merchant": dentist_merchant,
        "category": {"slug": "dentists", "target_digest_item": {"title": "Fluoride recall", "source": "JIDA Oct 2026"}}
    })
    assert "Dr. Dr." not in digest["body"]
    assert digest["body"].startswith("Dr. Meera,")

    # Non-dentist merchants must NOT get a "Dr." prefix
    salon = _generate_grounded_fallback({
        "trigger": {"kind": "perf_dip", "payload": {"metric": "calls", "delta_pct": -0.25}},
        "merchant": {"name": "Studio Eleven", "owner_first_name": "Riya", "category_slug": "salons"},
        "category": {"slug": "salons"}
    })
    assert salon["body"].startswith("Hi Riya,")
    assert "Dr." not in salon["body"]


def test_validator_scrubs_internal_snake_case_jargon_from_body():
    """Leaked internal snake_case tokens in the body are humanized; template_name stays snake_case."""
    from vera.validator import output_validator, _scrub_internal_jargon

    # Unit-level: the scrubber humanizes multi-part snake_case, leaves plain words alone
    assert _scrub_internal_jargon("Try shelf_action_recommended now") == "Try shelf action recommended now"
    assert _scrub_internal_jargon("free_for_members offer") == "free for members offer"
    assert _scrub_internal_jargon("no underscores here") == "no underscores here"

    projection = {
        "merchant": {"name": "Apollo Pharmacy", "locality": "Jaipur", "owner_first_name": "Vikas"},
        "trigger": {"kind": "perf_dip", "payload": {"metric": "views", "delta_pct": -0.20}}
    }
    raw = {
        "body": "Hi Vikas, views dipped 20% in Jaipur. I suggest shelf_action_recommended. Want the steps?",
        "cta": "binary_yes_no",
        "send_as": "vera",
        "template_name": "vera_perf_dip_v1",
        "rationale": "test"
    }
    validated = output_validator.validate_and_repair_proactive(
        raw,
        expected_send_as="vera",
        expected_cta="binary_yes_no",
        template_name="vera_perf_dip_v1",
        previous_body_hashes=[],
        projection=projection
    )
    assert validated is not None
    assert "shelf_action_recommended" not in validated["body"]
    assert "shelf action recommended" in validated["body"]
    # The template_name is a legitimate internal identifier and must remain snake_case
    assert validated["template_name"] == "vera_perf_dip_v1"






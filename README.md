# magicpin Vera AI Challenge — Candidate Bot Implementation

## 1. Approach Overview

This solution implements a deterministic, stateful message composition engine for magicpin's Vera assistant. The architecture strictly decouples decision routing, state tracking, and minimal context projection from LLM generation:

```
[Inbound Context / Triggers]
            │
    ┌───────▼────────┐
    │  Context Store │ (Versioned atomic upsert & idempotency)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Trigger Router │ (Urgency scoring, consent & suppression checks)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │Context Selector│ (Minimal token projection per vertical & trigger)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Strategy & LLM │ (Multi-provider abstraction: Gemini / OpenAI / Groq / Fallback)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │Rule Validator  │ (Deterministic validation, repair, & anti-repetition)
    └───────┬────────┘
            │
   [Grounded WhatsApp Action]
```

## 2. Key Architecture Decisions & Tradeoffs

1. **Context Projection over Monolithic Injection**:
   - Rather than dumping the full multi-megabyte dataset or full digests into prompts, `context_selector.py` isolates only linked merchant identity, exact metrics/deltas, category voice profile, and the single targeted digest item.
   - *Tradeoff*: Context projection substantially reduces prompt size and eliminates token waste while bounding processing latency; external LLM latency remains provider-dependent.

2. **Stateful Conversation State Machine**:
   - Maintains conversation history, body hashes (to guarantee anti-repetition), auto-reply counters per merchant, and explicit opt-out status.
   - Immediate **Intent Transition**: If the merchant responds with acceptance ("yes", "let's do it", "send it"), the bot switches immediately from pitch mode to action mode without re-qualifying.
   - **Auto-Reply Protection**: Repeated canned phrases ("Thank you for contacting...") trigger a wait back-off and subsequent graceful exit to avoid loop spam.

3. **Deterministic Multi-Provider LLM & Resilient Grounded Generation**:
   - The engine supports Gemini, OpenAI, Groq, and local testing providers configured via environment variables.
   - If an LLM call fails or times out, the engine executes a deterministic contextual composition based directly on the projected facts, ensuring responses always remain grounded, non-hallucinated, and comfortably below the 30s deadline.

## 3. Endpoints Implemented

- `GET /v1/healthz`: Uptime monitoring and dynamic counts across all 4 context scopes (`category`, `merchant`, `customer`, `trigger`).
- `GET /v1/metadata`: Bot identification, team details, model selection, and version metadata.
- `POST /v1/context`: Strict versioning: same version is idempotent no-op; higher version atomically replaces prior state; lower version returns `409 Conflict` (`stale_version`).
- `POST /v1/tick`: Evaluates available triggers against suppression keys and consent, produces up to 20 ranked, grounded proactive WhatsApp actions with template metadata.
- `POST /v1/reply`: Handles multi-turn simulation, distinguishing auto-replies, opt-outs, off-topic inquiries, and commitment-to-action transitions.
- `POST /v1/teardown`: Cleanly wipes in-memory caches for test harness replay isolation.

## 4. Running the Bot & Tests

### Start the Service:
```bash
uvicorn vera.app:app --host 0.0.0.0 --port 8080
```
Or directly:
```bash
python bot.py
```

### Run Unit & Behavioral Tests:
```bash
python -m pytest tests/test_service.py -v
```

### Run the Official Judge Simulator:
```bash
python judge_simulator.py
```

### Generate 30-Pair Submission Artifact:
```bash
python generate_submission.py
```

## 5. What Additional Context Would Have Helped
- Real-time booking calendar slot integration for appointments rather than synthetic slots.
- Historical CTR performance per specific creative template across micro-localities to improve predictive offer ranking.

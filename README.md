# magicpin Vera AI Challenge — Candidate Bot Implementation

## 1. Architecture Overview

This solution implements a deterministic, stateful, context-grounded message composition engine for magicpin's Vera assistant. The architecture decouples trigger eligibility, multi-factor decision ranking, minimal context projection, async LLM generation, and deterministic fact verification:

```
[Inbound Context / Triggers]
            │
    ┌───────▼────────┐
    │  Context Store │ (Thread-safe versioned upsert & 409 conflict detection)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Trigger Router │ (Simulated now expiry, consent check, suppression & multi-factor ranking)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │Context Selector│ (Minimal token projection per vertical & trigger kind)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Async LLM / FB │ (Async httpx client with provider abstraction: Gemini / OpenAI / Groq)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Fact Validator │ (FactRegistry boundary check for numbers/currency/%/dates & anti-repetition)
    └───────┬────────┘
            │
   [Grounded WhatsApp Action]
```

## 2. Key Architectural Components

1. **Context Projection over Monolithic Injection**:
   - Rather than dumping multi-megabyte payloads or full digests into prompts, `context_selector.py` isolates only linked merchant identity, exact metrics/deltas, category voice profile, and the single targeted digest item.
   - Substantially minimizes prompt size, avoids context overflow, and keeps execution latency bounded.

2. **Stateful Conversation State Machine**:
   - Maintains conversation history, body SHA-256 hashes (to guarantee anti-repetition), conversation-scoped auto-reply tracking, and explicit opt-out status.
   - **Immediate Terminal Opt-Out**: Verified before classification or LLM execution; stops all further messaging immediately upon opt-out signals.
   - **Intent Transition**: On merchant acceptance ("yes", "let's do it", "send it"), switches immediately to `ACTION` mode without re-qualifying.
   - **Trigger Preservation on Replies**: Re-attaches original trigger facts (e.g. specific percentage drop, research citation, milestone) so user questions are answered with exact context.

3. **Multi-Tier Grounding & Fact Validation**:
   - `FactRegistry` extracts allowable numbers, currencies, percentages, dates, and named entities from the projected context.
   - `OutputValidator` inspects all outputs before return. Any ungrounded claims or hallucinated figures are automatically replaced with a grounded contextual fallback.

4. **Non-Blocking Async Execution**:
   - All external LLM requests use `httpx.AsyncClient` with bounded timeouts (10s), ensuring the FastAPI event loop remains responsive under concurrent requests.
   - `/v1/tick` prioritizes candidate triggers to top actionable items per tick to prevent latency budget exhaustion.

## 3. Endpoints Implemented

- `GET /v1/healthz`: Uptime monitoring and dynamic counts across all 4 context scopes (`category`, `merchant`, `customer`, `trigger`).
- `GET /v1/metadata`: Bot identification, team details, model selection, and configurable contact metadata.
- `POST /v1/context`: Strict versioning: same version is idempotent no-op (200 OK); higher version atomically replaces prior state; lower version returns `409 Conflict` (`stale_version`).
- `POST /v1/tick`: Evaluates candidate triggers against `now` timestamp, consent status, and suppression keys; produces high-confidence proactive WhatsApp actions with template metadata.
- `POST /v1/reply`: Handles multi-turn simulation, distinguishing auto-replies, terminal opt-outs, off-topic inquiries, and commitment-to-action transitions.
- `POST /v1/teardown`: Cleanly wipes in-memory stores for test harness replay isolation.

## 4. Running the Bot & Tests

### Install Dependencies:
```bash
pip install -r requirements.txt
```

### Start the Service:
```bash
python bot.py
```
Or via uvicorn directly:
```bash
uvicorn vera.app:app --host 0.0.0.0 --port 8080
```

### Run Unit & Behavioral Test Suite (16 tests):
```bash
pytest tests/ -v
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

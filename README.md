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
    │ Trigger Router │ (Simulated-now expiry, consent scope, suppression & multi-factor ranking)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │Context Selector│ (Minimal token projection per vertical & trigger kind)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Async LLM / FB │ (Async httpx client with Gemini / OpenAI / Groq; grounded fallback on 429/timeout)
    └───────┬────────┘
            │
    ┌───────▼────────┐
    │ Fact Validator │ (FactRegistry boundary check for numbers/currency/%/distances/dates & anti-repetition)
    └───────┬────────┘
            │
   [Grounded WhatsApp Action]
```

## 2. Key Architectural Components

1. **Context Projection over Monolithic Injection**:
   - Rather than dumping multi-megabyte payloads or full digests into prompts, `context_selector.py` isolates only linked merchant identity, exact metrics/deltas, category voice profile, and the single targeted digest item.
   - Per-kind allowlists cover all trigger families: research, regulation, recall, performance, renewal, festival, wedding, appointment, milestone, competitor, winback, refill, IPL/event, and adaptive/unknown triggers.
   - Substantially minimizes prompt size, avoids context overflow, and keeps execution latency bounded.

2. **Stateful Conversation State Machine**:
   - Maintains conversation history, body SHA-256 hashes (to guarantee anti-repetition), conversation-scoped auto-reply tracking, and explicit opt-out status.
   - **Immediate Terminal Opt-Out**: Verified before classification or LLM execution; stops all further messaging immediately upon opt-out signals.
   - **Intent Transition**: On merchant acceptance ("yes", "let's do it", "send it"), switches immediately to `ACTION` mode without re-qualifying.
   - **Action Request Detection**: "Can you schedule it for Friday?" is correctly classified as `ACTION_REQUEST`, not as acceptance.
   - **Trigger Preservation on Replies**: Re-attaches original trigger facts (e.g. specific percentage drop, research citation, slot time) so user questions are answered with exact context.

3. **Multi-Tier Grounding & Fact Validation**:
   - `FactRegistry` extracts type-specific evidence sets: allowed prices (`₹`), distances (`km`), sample sizes (`n=`), percentages (`%`), dates, offer titles, and regulatory sources from the projected context.
   - `OutputValidator` enforces strategy send_as and CTA unconditionally — the LLM cannot override these.
   - Inspects all proactive and reply outputs before dispatch. Any ungrounded claims or hallucinated figures are replaced with grounded contextual fallbacks that are themselves strictly revalidated; ungroundable messages are safely suppressed.

4. **Non-Blocking Async Execution**:
   - All external LLM requests use `httpx.AsyncClient` with bounded timeouts (10 s), ensuring the FastAPI event loop remains responsive under concurrent requests.
   - HTTP 429 (quota exhausted), timeouts, and transient errors immediately trigger the deterministic grounded fallback — no retries, no storms.
   - `/v1/tick` limits candidate composition to top 2 actionable triggers per tick to prevent latency budget exhaustion.

5. **Free-Tier Safe Gemini Integration**:
   - Uses `gemini-3.8-flash` via the official REST GenerateContent endpoint with structured JSON output requested.
   - No retry logic on 429 — falls back to deterministic generation immediately.
   - No Google Search grounding or other paid-only features.
   - Bot remains fully functional with no API key (deterministic fallback mode).

## 3. Endpoints Implemented

- `GET /v1/healthz`: Uptime monitoring and dynamic counts across all 4 context scopes (`category`, `merchant`, `customer`, `trigger`).
- `GET /v1/metadata`: Bot identification, team details, model selection, and configurable contact metadata.
- `POST /v1/context`: Strict versioning: same version is idempotent no-op (200 OK); higher version atomically replaces prior state; lower version returns `409 Conflict` (`stale_version`).
- `POST /v1/tick`: Evaluates candidate triggers against `now` timestamp, consent status, and suppression keys; produces high-confidence proactive WhatsApp actions with template metadata.
- `POST /v1/reply`: Handles multi-turn simulation, distinguishing auto-replies, terminal opt-outs, off-topic inquiries, scheduling questions, and commitment-to-action transitions.
- `POST /v1/teardown`: Cleanly wipes in-memory stores for test harness replay isolation.

## 4. Gemini API Configuration

Obtain a free API key at [Google AI Studio](https://aistudio.google.com/) and set these environment variables:

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=<your key here>
GEMINI_MODEL=gemini-3.8-flash
```

Copy `.env.example` to `.env` and fill in your key. **Never commit `.env` or your real key.**

Without a key, the bot runs in deterministic grounded fallback mode and remains fully functional.

## 5. Running the Bot & Tests

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

### Run Unit & Behavioral Test Suite (20 tests):
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

## 6. What Additional Context Would Have Helped
- Real-time booking calendar slot integration for appointments rather than synthetic slots.
- Historical CTR performance per specific creative template across micro-localities to improve predictive offer ranking.

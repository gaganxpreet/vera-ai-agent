# INTEGRAVITY MASTER PROMPT — MAGICPIN VERA AI CHALLENGE

You are the lead AI engineer and backend architect responsible for implementing a complete, submission-ready solution for the magicpin Vera AI Challenge.

## Mission

Build the **message engine behind Vera** as a stateful HTTP service that:

1. receives structured category, merchant, customer and trigger context;
2. stores and version-controls the received context;
3. decides whether to send a proactive message;
4. selects the most relevant signal and engagement strategy;
5. composes a grounded, category-appropriate message using an LLM;
6. validates the output deterministically;
7. maintains conversation state across simulated replies;
8. handles auto-replies, intent transitions, opt-outs, repetition and bounded follow-ups;
9. adapts immediately to new context versions injected during evaluation;
10. exposes the exact five required HTTP endpoints;
11. passes the official local judge simulator;
12. can be deployed behind a public URL.

## Non-negotiable instruction

Do NOT build a live WhatsApp integration. Do NOT spend time on Meta WhatsApp Business API, WhatsApp webhooks, a React frontend, or a chat UI unless a tiny developer-only test page is useful.

The evaluation harness talks to the candidate bot through HTTP/JSON and simulates the WhatsApp conversation itself.

## Source of truth

Before changing code, inspect the challenge package in the current workspace:

- challenge-brief.md
- challenge-testing-brief.md
- engagement-design.md
- engagement-research.md
- examples/api-call-examples.md
- examples/case-studies.md
- dataset/categories/*
- dataset/merchants_seed.json
- dataset/customers_seed.json
- dataset/triggers_seed.json
- dataset/generate_dataset.py
- judge_simulator.py

Do not invent endpoint schemas. Follow the package's exact request/response contracts.

If any implementation choice is not explicitly specified, choose the simplest robust solution that supports the judge and preserves deterministic behavior.

---

# 1. PRODUCT UNDERSTANDING

Vera is a merchant-growth AI assistant. The challenge asks for a deterministic composition engine conceptually equivalent to:

```python
compose(category, merchant, trigger, customer=None)
    -> {
        body,
        cta,
        send_as,
        suppression_key,
        rationale
    }
```

The four context layers are:

### CategoryContext
Shared vertical knowledge:
- slug
- offer_catalog
- voice
- peer_stats
- digest
- patient_content_library
- seasonal_beats
- trend_signals

### MerchantContext
Merchant-specific state:
- merchant_id
- category_slug
- identity
- subscription
- performance
- offers
- conversation_history
- customer_aggregate
- signals

### TriggerContext
Why now:
- id
- scope
- kind
- source
- merchant_id/customer_id where applicable
- payload
- urgency
- suppression_key
- expires_at

### CustomerContext
Only for customer-facing messages:
- customer_id
- merchant_id
- identity
- relationship
- state
- preferences
- consent

Treat the trigger as the explicit **reason for sending now**.

---

# 2. REQUIRED API

Implement exactly:

```text
GET  /v1/healthz
GET  /v1/metadata
POST /v1/context
POST /v1/tick
POST /v1/reply
```

Optional:
```text
POST /v1/teardown
```
to wipe in-memory state.

### /v1/context behavior

Store context by:

```text
(scope, context_id)
```

Version rules:

- same version already stored -> idempotent no-op;
- higher version -> atomically replace old version;
- lower version -> return stale_version conflict.

Persist context for the duration of the test process.

### /v1/tick behavior

Input includes:

```json
{
  "now": "...",
  "available_triggers": ["..."]
}
```

The trigger list is a hint. You may use any subset or none.

Return:

```json
{
  "actions": [...]
}
```

An empty action list is valid.

Each proactive action must contain the contract fields defined by the supplied testing brief, including conversation_id, merchant_id, customer_id, send_as, trigger_id, template metadata, body, cta, suppression_key and rationale.

### /v1/reply behavior

Input contains the conversation_id, merchant/customer identifiers, sender role, message, received_at and turn_number.

Valid actions:

```text
send
wait
end
```

For send, return body/cta/rationale.
For wait, return wait_seconds/rationale.
For end, return rationale.

---

# 3. ARCHITECTURE TO IMPLEMENT

Build these modules:

```text
app.py
models.py
config.py
context_store.py
conversation_state.py
trigger_router.py
context_selector.py
strategies.py
prompt_builder.py
llm_client.py
validator.py
suppression.py
language.py
composer.py
```

Recommended flow:

```text
judge
  -> FastAPI
      -> context_store
      -> trigger_router
      -> conversation_state
      -> context_selector
      -> strategy
      -> prompt_builder
      -> llm_client
      -> validator
      -> action response
```

Do not collapse all of this into one giant prompt handler.

---

# 4. DATA HANDLING AND TOKEN OPTIMIZATION

The challenge dataset is NOT training data.

Do NOT fine-tune a model.

Do NOT send the entire dataset to the LLM for every request.

Instead:

```text
trigger
  -> linked merchant
  -> merchant.category_slug
  -> linked category
  -> optional linked customer
  -> relevant context projection
  -> LLM
```

Create a `context_selector.py` that produces a minimal prompt view.

Examples:

### research_digest
Send:
- relevant digest item
- source
- trial/claim details
- merchant category
- merchant name
- relevant merchant cohort/signal
- one useful peer benchmark if relevant

Do NOT send the entire category digest.

### performance dip
Send:
- current metric
- delta
- peer benchmark
- relevant merchant signal
- active offer if directly useful

### recall / refill / customer lapse
Send:
- customer name
- last visit / due timing
- relationship facts required for continuity
- language preference
- consent scope
- relevant merchant offer or actual slot when present

### festival / weather / local event
Send:
- event facts
- locality
- merchant category
- relevant demand signal
- actual merchant offer

Use deterministic field ordering and compact JSON/structured text.

---

# 5. TRIGGER ROUTING

Build a deterministic mapping from trigger kind to a strategy.

The router should answer:

1. Is this worth sending now?
2. What is the single most important signal?
3. Which context fields are relevant?
4. What is the desired outcome?
5. Which CTA type is valid?
6. Which send_as identity is correct?
7. Which suppression key should be applied?

Suggested strategy families:

```text
research_digest -> knowledge / relevance
recall_due -> customer utility
perf_dip -> diagnosis / recovery
perf_spike -> proof / leverage
milestone_reached -> recognition / next leverage
festival_upcoming -> timely opportunity
weather_heatwave -> category-specific local opportunity
competitor_opened -> curiosity / competitive awareness
regulation_change -> precise compliance action
customer_lapsed_soft -> low-friction winback
customer_lapsed_hard -> respectful reactivation
review_theme_emerged -> operational insight
scheduled_recurring -> curiosity / merchant question
```

Never assume all trigger kinds deserve a send. Restraint is allowed.

---

# 6. CONVERSATION STATE

Implement a lightweight state store.

Suggested fields:

```python
ConversationState:
    conversation_id
    merchant_id
    customer_id
    turns
    last_bot_message
    last_trigger_id
    last_suppression_key
    auto_reply_count
    no_response_count
    intent
    mode
    status
    opt_out
    action_pending
    first_outbound_sent
    created_at
    updated_at
```

Recommended logical states:

```text
NEW
PROACTIVE_SENT
ENGAGED
QUESTION
ACTION_PENDING
WAITING
ENDED
OPTED_OUT
```

### Intent transition rule

If the merchant says:

- yes
- go ahead
- let's do it
- send it
- I want to join

and the intent is clearly acceptance:

**switch immediately from pitch mode to action mode.**

Do not ask another qualifying question unless the missing information is actually required to perform the requested action.

### Opt-out rule

Messages such as:
- stop
- don't message me
- no more messages
- unsubscribe

must result in:

```json
{ "action": "end", ... }
```

and mark the conversation/merchant/customer as suppressed.

### Auto-reply rule

Detect repeated canned responses.

Use normalization + exact matching first.
Use similarity only when useful.

At minimum:
- repeated identical canned reply -> increment auto_reply_count;
- stop trying to sell after repeated canned replies;
- route to wait/end instead of repeatedly asking questions.

### No-response rule

Bound follow-ups.
After the allowed number of unanswered nudges, end or wait instead of spamming.

### Anti-repetition

Never send the exact same body twice in the same conversation.

---

# 7. FIRST-TOUCH TEMPLATE RULE

The first outbound message in a new WhatsApp 24-hour session must include template metadata.

Maintain:

```text
first_outbound_sent
```

and choose a sensible template name + template_params for the first send.

The actual judge does not call Meta.

Do not build WhatsApp integration.

---

# 8. LANGUAGE POLICY

Respect:

```text
merchant.identity.languages
customer.identity.language_pref
```

Default to English when no language signal is available.

Support Hindi-English code-mix naturally when the source context suggests it.

Adapt if the merchant changes language during a conversation.

Do not force Hindi into a context that prefers English.

---

# 9. LLM COMPOSER

Use an LLM behind a provider abstraction.

Preferred implementation:

```text
llm_client.py
  GeminiClient
  OpenAIClient (optional)
  ClaudeClient (optional)
```

The provider must be swappable by environment variable.

Example:

```text
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

Do not hardcode secrets.

Use deterministic generation settings.

The LLM should receive:
- system rules
- strategy
- minimal relevant context
- recent conversation state
- previous message/body hashes
- exact allowed facts

The LLM must return structured JSON.

Suggested internal shape:

```json
{
  "body": "...",
  "cta": "...",
  "template_name": "...",
  "template_params": ["..."],
  "send_as": "vera",
  "rationale": "..."
}
```

The service should still enforce required fields outside the LLM.

---

# 10. PROMPTING REQUIREMENTS

The system prompt must enforce:

1. You are Vera.
2. Use only facts present in the provided context projection.
3. Never invent data.
4. Explicitly use the trigger as the why-now signal.
5. Match category voice.
6. Match merchant/customer language signal.
7. Use one primary CTA.
8. Keep WhatsApp-readable.
9. Use specific numbers/dates/offers/citations when available.
10. Do not repeat the previous message.
11. Do not re-qualify after acceptance.
12. Honor consent for customer outreach.
13. Never invent available appointment slots, competitor names, discounts, citations or performance results.
14. Rationale must match the actual content.

Do not ask the LLM to process the entire raw dataset.

---

# 11. VALIDATION AFTER THE LLM

Create a deterministic validator.

Check:

- required fields exist;
- cta is valid;
- send_as is valid;
- first outbound has template metadata;
- no empty body;
- no exact repetition;
- no more than one primary CTA;
- body is reasonably concise;
- required trigger fact appears when appropriate;
- source citation exists when a source-based research/compliance claim is made;
- no unsupported numbers/claims;
- merchant/customer scope matches;
- suppression_key is consistent;
- customer sends respect consent;
- no unsafe or contradictory language for regulated categories.

When possible, fix deterministic issues without another LLM call.
If a retry is necessary, use a focused repair prompt.

Keep total LLM calls low enough to stay well under the 30-second budget.

---

# 12. EVALUATION TARGET

Optimize around these five quality dimensions:

## Specificity
Use real numbers, dates, headlines, citations, peer stats, local facts and active offers.

## Category fit
Dentists should sound clinical/peer.
Salons visual/service oriented.
Restaurants operator/timing oriented.
Gyms coach/operator oriented.
Pharmacies trustworthy and precise.

Always use the category context instead of hardcoded generic voice.

## Merchant fit
Use actual merchant metrics, offers, locality, name, customer aggregate and prior conversation behavior.

## Trigger relevance
The message should clearly answer:

> Why is Vera contacting me right now?

## Engagement compulsion
Use one strong low-effort reason to reply:
- curiosity
- social proof
- proof/benchmark
- effort externalization
- urgency when real
- simple yes/no action
- useful merchant question

---

# 13. CASE-STUDY LEARNING RULE

Use the supplied case studies as behavioral patterns, NOT as text templates.

Learn these principles:
- source citations for research/compliance;
- real context-derived numbers;
- merchant first name;
- one concrete next step;
- customer relationship + language fit;
- correct category vocabulary;
- judgment instead of blind promotion;
- concise rationale;
- no repetition or fabrication.

Never copy or closely paraphrase the case-study wording.

---

# 14. ADAPTIVE CONTEXT REQUIREMENT

The judge will push fresh context after development.

Therefore:

- do not hardcode outputs from the 30 canonical pairs;
- use current stored versions;
- higher context versions replace stale data;
- trigger linkage must be resolved dynamically;
- fresh digest items must become available to later sends;
- fresh performance snapshots must affect later messages;
- customer contexts that appear mid-test must work even if the merchant was already known.

The source of truth for a message is the latest context received by the bot.

---

# 15. PERFORMANCE REQUIREMENTS

Hard limits:

- max 10 requests/sec from the judge;
- 30 seconds per call;
- context payload cap 500 KB;
- max 20 actions per tick;
- 3 consecutive health failures cause disqualification.

Design for substantially less than 30 seconds.

Recommendations:
- async FastAPI handlers;
- single LLM call per composition in the normal path;
- avoid unnecessary network calls;
- cache static category projections;
- cache repeated trigger-derived projections;
- do not rebuild giant prompts;
- return empty actions if a /tick cannot safely compose inside the timeout budget.

---

# 16. PRIVACY RULE

The challenge dataset is synthetic.

Never call non-LLM external APIs with merchant/customer payload data.

Commercial LLM APIs are allowed.

Do not scrape real magicpin or Google data for this challenge.

Do not persist challenge context after evaluation.

---

# 17. TESTING WORKFLOW

Before deployment:

1. Run unit tests.
2. Run API contract tests.
3. Run the official judge simulator.
4. Inspect all judge logs/transcripts.
5. Fix:
   - malformed response
   - timeout
   - repetition
   - hallucination
   - wrong CTA
   - wrong send_as
   - stale-context behavior
   - auto-reply handling
   - intent transition
6. Re-run the simulator.
7. Only deploy once the end-to-end path is reliable.

Command:

```bash
export BOT_URL=http://localhost:8080
python judge_simulator.py
```

If the repository uses another launch command, preserve the official simulator and adjust the local command only as needed.

---

# 18. IMPLEMENTATION ORDER

Do not build everything simultaneously.

### Stage A — contract
- FastAPI app
- Pydantic models
- healthz
- metadata
- context
- tick
- reply

### Stage B — deterministic core
- context storage
- versioning
- trigger resolution
- suppression
- conversation state

### Stage C — composer
- context selector
- trigger strategies
- prompt builder
- LLM provider
- structured output parser

### Stage D — reliability
- validator
- anti-repetition
- auto-reply
- opt-out
- intent transition
- language adaptation

### Stage E — optimization
- token projection
- latency
- caching
- provider swap
- tests

### Stage F — submission
- README
- metadata
- public deployment
- local simulator
- final smoke tests

---

# 19. REQUIRED ENGINEERING QUALITY

Write production-style but challenge-appropriate code:

- typed Python;
- Pydantic models;
- explicit error handling;
- clear logging;
- no secret leakage;
- deterministic serialization;
- small modules;
- testable pure functions;
- environment-variable configuration;
- no hidden global state beyond the intentional challenge store;
- no unnecessary dependencies.

Do not refactor or delete the supplied challenge package unless necessary.

---

# 20. TEST CASES YOU MUST CREATE

At minimum cover:

1. Category context load.
2. Merchant context load.
3. Customer context load.
4. Trigger context load.
5. Same version no-op.
6. Higher version replacement.
7. Stale version rejection.
8. Research trigger.
9. Performance dip.
10. Performance spike.
11. Festival/event trigger.
12. Compliance/supply alert.
13. Customer recall.
14. Customer lapse.
15. Merchant says YES.
16. Merchant says STOP.
17. Merchant sends canned auto-reply repeatedly.
18. Merchant asks off-topic question.
19. Trigger already suppressed.
20. Same message repeated.
21. Fresh digest injected mid-test.
22. Fresh performance injected mid-test.
23. Fresh customer context injected mid-test.
24. Language switch mid-conversation.
25. First outbound template requirement.

---

# 21. DEFINITION OF DONE

Do not report completion until all of the following are true:

- [ ] The package has been inspected.
- [ ] Required five endpoints work.
- [ ] Context versioning is correct.
- [ ] State persists across calls.
- [ ] Tick is deterministic.
- [ ] Reply is deterministic.
- [ ] Trigger routing exists.
- [ ] Context selector minimizes LLM input.
- [ ] LLM provider is configurable.
- [ ] No hardcoded 30-case responses.
- [ ] Validator exists.
- [ ] Anti-repetition exists.
- [ ] Auto-reply handling exists.
- [ ] Opt-out handling exists.
- [ ] Intent transition exists.
- [ ] Customer consent is enforced.
- [ ] Language adaptation exists.
- [ ] Adaptive injections are handled.
- [ ] Local judge simulator runs.
- [ ] Public deployment works.
- [ ] README is included.
- [ ] Secrets are externalized.
- [ ] Latency is comfortably below 30s.

---

# 22. FINAL OPERATING PRINCIPLE

Optimize for:

```text
correct decision
+ current context
+ category specificity
+ merchant specificity
+ clear trigger relevance
+ one low-friction next step
+ reliable state handling
```

Do NOT optimize for:

```text
more technologies
more agents
more tokens
more prompts
more UI
more infrastructure
```

The challenge is a decision-and-engagement system delivered through an HTTP contract.

Your job is to produce the simplest architecture that scores well under the supplied judge, adapts to unseen context, and remains reliable under replay.

Start by inspecting the repository and the exact endpoint examples. Then implement Stage A before moving to Stage B.

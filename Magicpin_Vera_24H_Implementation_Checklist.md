# Magicpin Vera — 24-Hour Implementation Checklist

## A. Understand the brief
- [ ] Read challenge-brief.md
- [ ] Read challenge-testing-brief.md
- [ ] Read engagement-design.md
- [ ] Read engagement-research.md
- [ ] Read API examples
- [ ] Read case studies
- [ ] Run dataset generator if needed
- [ ] Run judge simulator once before coding to understand the failure surface

## B. API
- [ ] GET /v1/healthz
- [ ] GET /v1/metadata
- [ ] POST /v1/context
- [ ] POST /v1/tick
- [ ] POST /v1/reply
- [ ] Optional /v1/teardown

## C. Context
- [ ] Versioned CategoryContext store
- [ ] Versioned MerchantContext store
- [ ] Versioned CustomerContext store
- [ ] Versioned TriggerContext store
- [ ] Same version => no-op
- [ ] Lower version => stale conflict
- [ ] Higher version => atomic replace

## D. Decision layer
- [ ] Trigger router
- [ ] Trigger-specific strategies
- [ ] Suppression/dedup
- [ ] Decide-send vs no-send
- [ ] Current-version resolution
- [ ] Trigger expiry handling

## E. LLM layer
- [ ] Provider abstraction
- [ ] Environment-based API key
- [ ] Deterministic generation
- [ ] Structured JSON output
- [ ] Compact context projection
- [ ] Single-call default path
- [ ] Repair call only when needed

## F. Conversation layer
- [ ] State persistence
- [ ] Intent detection
- [ ] YES/go-ahead => action mode
- [ ] STOP => end + suppress
- [ ] Auto-reply detection
- [ ] Unanswered nudge bound
- [ ] Language adaptation
- [ ] Anti-repetition
- [ ] First-touch template tracking

## G. Validation
- [ ] Valid cta
- [ ] Valid send_as
- [ ] Non-empty body
- [ ] One primary CTA
- [ ] Trigger-grounded message
- [ ] No fabricated numbers
- [ ] No fabricated citations
- [ ] No stale facts after version update
- [ ] Rationale matches message
- [ ] Customer consent check

## H. Scoring optimization
- [ ] Specificity
- [ ] Category fit
- [ ] Merchant fit
- [ ] Trigger relevance
- [ ] Engagement compulsion
- [ ] Decision quality
- [ ] No generic filler
- [ ] No case-study copying

## I. Judge resilience
- [ ] Fresh digest injection
- [ ] Fresh performance update
- [ ] New trigger injection
- [ ] New customer mid-test
- [ ] Auto-reply replay
- [ ] Intent transition replay
- [ ] Hostile/off-topic replay

## J. Operations
- [ ] <30s per call
- [ ] <=20 actions/tick
- [ ] 10 req/sec safe
- [ ] Health stable
- [ ] Public URL reachable
- [ ] Logs useful
- [ ] No secret committed
- [ ] Judge simulator passes

## K. Submission
- [ ] bot.py / service code
- [ ] README
- [ ] Any required JSONL/artifacts
- [ ] Public URL
- [ ] Metadata updated
- [ ] Final smoke test

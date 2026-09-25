import time
from datetime import datetime, timezone
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from vera.config import settings
from vera.models import (
    HealthResponse, ContextCounts, MetadataResponse,
    ContextPushRequest, ContextPushSuccess, ContextPushConflict,
    TickRequest, TickResponse, ProactiveAction,
    ReplyRequest, ReplyResponse
)
from vera.context_store import context_store
from vera.conversation_state import conversation_store
from vera.suppression import suppression_manager
from vera.trigger_router import trigger_router
from vera.composer import composer

START_TIME = time.time()

app = FastAPI(
    title="magicpin Vera AI Message Engine",
    description="Stateful, deterministic, grounded message engine for Vera merchant growth assistant.",
    version=settings.version
)

@app.get("/v1/healthz", response_model=HealthResponse)
async def healthz():
    uptime = int(time.time() - START_TIME)
    counts = context_store.counts()
    return HealthResponse(
        status="ok",
        uptime_seconds=uptime,
        contexts_loaded=ContextCounts(**counts)
    )

@app.get("/v1/metadata", response_model=MetadataResponse)
async def metadata():
    return MetadataResponse(
        team_name=settings.team_name,
        team_members=settings.team_members,
        model=settings.model_name,
        approach=settings.approach,
        contact_email=settings.contact_email,
        version=settings.version,
        submitted_at="2026-04-26T08:00:00Z"
    )

@app.post("/v1/context")
async def push_context(req: ContextPushRequest):
    accepted, ack_or_reason, curr_ver = context_store.upsert(
        scope=req.scope,
        context_id=req.context_id,
        version=req.version,
        payload=req.payload
    )
    if not accepted:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"accepted": False, "reason": "stale_version", "current_version": curr_ver}
        )
    return {
        "accepted": True,
        "ack_id": ack_or_reason,
        "stored_at": datetime.now(timezone.utc).isoformat()
    }

@app.post("/v1/tick", response_model=TickResponse)
async def tick(req: TickRequest):
    # Trigger routing & ranking
    ranked_triggers = trigger_router.evaluate_triggers(req.available_triggers)
    
    # Cap at 20 actions per tick as per challenge specs
    max_actions = 20
    actions: List[ProactiveAction] = []
    
    for item in ranked_triggers[:max_actions]:
        tid = item["trigger_id"]
        t_data = item["trigger"]
        strategy = item["strategy"]
        
        try:
            action = composer.compose_proactive_action(tid, t_data, strategy)
            if action:
                actions.append(action)
        except Exception as e:
            import traceback
            print(f"Error in compose_proactive_action: {e}")
            traceback.print_exc()
            continue

    return TickResponse(actions=actions)

@app.post("/v1/reply", response_model=ReplyResponse)
async def reply(req: ReplyRequest):
    resp = composer.compose_reply(
        conversation_id=req.conversation_id,
        merchant_id=req.merchant_id,
        customer_id=req.customer_id,
        from_role=req.from_role,
        inbound_message=req.message,
        turn_number=req.turn_number
    )
    return resp

@app.post("/v1/teardown")
async def teardown():
    """Wipes in-memory state cleanly for test restarts."""
    context_store.clear()
    conversation_store.clear()
    suppression_manager.clear()
    return {"status": "ok", "message": "State wiped cleanly"}

"""Pepperton observatory server — FastAPI + websocket + the map page."""

import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

import config
from sim.engine import Engine

app = FastAPI(title="Pepperton")
engine: Engine = None
_STATIC = os.path.join(os.path.dirname(__file__), "static")


@app.on_event("startup")
async def _startup():
    global engine
    state = Engine.load_state()
    engine = Engine(seed=config.WORLD_SEED, state=state)
    engine.start_background()


@app.get("/")
async def index():
    return FileResponse(os.path.join(_STATIC, "index.html"))


@app.get("/api/state")
async def state(since: int = 0):
    return JSONResponse(engine.snapshot(since_seq=since))


@app.get("/api/agent/{name}")
async def agent(name: str):
    data = engine.inspect(name)
    if data is None:
        return JSONResponse({"error": "no such villager"}, status_code=404)
    return JSONResponse(data)


@app.post("/api/control/pause")
async def pause():
    engine.paused = not engine.paused
    return {"paused": engine.paused}


@app.post("/api/recast")
async def recast(body: dict):
    """Brain transplant: swap which model plays a villager, mid-run.
    Body: {"agent": "<name>", "model": "<ollama tag>", "host": "<host key>"}.
    Memories, money, and relationships stay — only the mind changes."""
    from sim.brains import MockBrain, OllamaBrain
    name = body.get("agent", "")
    resolved = engine.world._resolve_agent(name)
    if not resolved:
        return JSONResponse({"error": f"no villager matching {name!r}"}, status_code=404)
    model = body.get("model")
    if not model:
        return JSONResponse({"error": "need a model tag"}, status_code=400)
    host = body.get("host", "default")
    if host not in config.OLLAMA_HOSTS:
        return JSONResponse({"error": f"unknown host key {host!r}"}, status_code=400)
    with engine.lock:
        agent = engine.world.agents[resolved]
        core = OllamaBrain(model, host)
        core.understudy = MockBrain(resolved, engine.seed)
        engine.brains[resolved].understudy = core
        old = agent.model
        agent.model, agent.host = model, host
    engine.world.emit("world", None,
                      f"(something subtle changes behind {resolved}'s eyes)",
                      agent.location, deliver=False)
    return {"recast": resolved, "was": old, "now": model, "host": host}


@app.post("/api/chaos")
async def chaos(body: dict = None):
    """Manually fire a Director event. Body: {"event": "stranger"} or empty
    for a random weighted roll. You are the god of this town; use it wisely."""
    name = (body or {}).get("event")
    with engine.lock:
        result = engine.director.trigger(name)
    return {"happened": result}


# ---------------------------------------------------------------- possession
@app.get("/api/possess/{name}/observation")
async def possess_observe(name: str):
    b = engine.brains.get(name)
    if not b:
        return JSONResponse({"error": "no such villager"}, status_code=404)
    return JSONResponse(b.latest_observation)


@app.post("/api/possess/{name}")
async def possess(name: str, body: dict):
    """Body: {"possess": true/false} to take/release the seat,
    or an action object {"action": ...} to queue a move."""
    b = engine.brains.get(name)
    if not b:
        return JSONResponse({"error": "no such villager"}, status_code=404)
    if "possess" in body:
        with engine.lock:
            b.possessed = bool(body["possess"])
        return {"possessed": b.possessed}
    if "action" in body:
        with engine.lock:
            b.queued_action = body
            b.possessed = True
        return {"queued": True}
    return JSONResponse({"error": "send {'possess': bool} or an action"}, status_code=400)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    last_seq = 0
    try:
        while True:
            snap = engine.snapshot(since_seq=last_seq)
            if snap["events"]:
                last_seq = snap["events"][-1]["seq"]
            await sock.send_json(snap)
            await asyncio.sleep(config.WS_PUSH_INTERVAL)
    except (WebSocketDisconnect, RuntimeError):
        pass

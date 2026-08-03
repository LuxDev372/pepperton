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
def agent(name: str):
    # Deliberately sync (not async): engine.inspect waits up to two seconds
    # on the tick lock, and inside an async handler that blocks uvicorn's
    # whole event loop — every other request, the pause button included,
    # queues behind it. A plain def runs in FastAPI's threadpool instead.
    data = engine.inspect(name)
    if data is None:
        return JSONResponse({"error": "no such villager"}, status_code=404)
    return JSONResponse(data)


@app.post("/api/control/pause")
async def pause():
    # Deliberately NOT under engine.lock. In live mode a tick holds that
    # lock while the models think, so taking it here left Pause ignoring
    # the operator for up to the Ollama timeout — worse than the benign
    # race two simultaneous flips could cause. The loop reads the flag
    # once per tick.
    engine.paused = not engine.paused
    return {"paused": engine.paused}


@app.post("/api/recast")
async def recast(body: dict):
    """Brain transplant: swap which model plays a villager, mid-run.
    Body: {"agent": "<name>", "model": "<model tag>", "host": "<provider key>"}.
    Memories, money, and relationships stay — only the mind changes."""
    from sim.brains import LLMBrain, MockBrain, providers
    name = body.get("agent", "")
    resolved = engine.world._resolve_agent(name)
    if not resolved:
        return JSONResponse({"error": f"no villager matching {name!r}"}, status_code=404)
    model = body.get("model")
    if not model:
        return JSONResponse({"error": "need a model tag"}, status_code=400)
    host = body.get("host", "default")
    known = providers()
    if host not in known:
        return JSONResponse(
            {"error": f"unknown provider key {host!r}",
             "known": sorted(known)}, status_code=400)
    old = engine.world.agents[resolved].model

    def apply():
        agent = engine.world.agents[resolved]
        core = LLMBrain(model, host)
        core.understudy = MockBrain(resolved, engine.seed)
        engine.brains[resolved].understudy = core
        agent.model, agent.host = model, host
        engine.world.emit("world", None,
                          f"(something subtle changes behind {resolved}'s eyes)",
                          agent.location, deliver=False)

    engine.submit(apply, f"recast {resolved} -> {model}")
    return {"recast": resolved, "was": old, "now": model, "host": host,
            "queued": True}


@app.get("/api/casts")
async def casts():
    """What minds and providers this town can draw on — so a control panel
    can offer a real dropdown instead of asking you to type model tags."""
    from sim.brains import providers
    known = providers()
    pool = {}
    for name, entries in getattr(config, "CASTS", {}).items():
        for model, host in entries:
            pool.setdefault(f"{model}@{host}", {"model": model, "host": host,
                                                "casts": []})
            pool[f"{model}@{host}"]["casts"].append(name)
    return {
        "cast": getattr(config, "CAST", None),
        "providers": {k: v.get("api", "ollama") for k, v in known.items()},
        "minds": sorted(pool.values(), key=lambda m: m["model"]),
        "chaos": sorted(config.CHAOS.get("weights", {})),
    }


@app.post("/api/chaos")
async def chaos(body: dict = None):
    """Manually fire a Director event. Body: {"event": "stranger"} or empty
    for a random weighted roll. You are the god of this town; use it wisely."""
    name = (body or {}).get("event")
    known = set(config.CHAOS.get("weights", {}))
    if name and name not in known:
        return JSONResponse({"error": f"unknown event {name!r}",
                             "known": sorted(known)}, status_code=400)
    # Queued rather than fired inline: see Engine.submit. Whatever the
    # Director decides shows up in the transcript a tick later.
    engine.submit(lambda: engine.director.trigger(name), f"chaos {name or 'random'}")
    return {"queued": True, "event": name or "a random roll"}


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
    # Seat changes touch only the brain wrapper, never the world, so they
    # apply immediately — the seat has to answer a click even mid-tick.
    if "possess" in body:
        b.possessed = bool(body["possess"])
        return {"possessed": b.possessed}
    if "action" in body:
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

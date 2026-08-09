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


@app.on_event("shutdown")
async def _shutdown():
    """Close the books on the way out (v3.2.1).

    Nothing called engine.stop() — so ExperimentLedger.close() never ran,
    and every run in data/experiments.json stayed marked "running" forever,
    with no closed_utc and no closing state hash. A town restarted on Day
    111 would leave an eleven-day window looking like it had never ended.

    The ledger exists so a run cannot lie about itself afterward. A run
    that cannot say it finished is a run lying about itself afterward.

    Also takes a final checkpoint: the loop autosaves every 4 ticks, so a
    clean shutdown was quietly discarding up to three ticks of a town's
    life for no reason.
    """
    if engine is None:
        return
    try:
        engine.save_state()    # checkpoint FIRST — close() hashes the file
        engine.stop()          # ...so a closing hash describes the final state
    except Exception:      # a failing shutdown must not hold the door
        import traceback
        traceback.print_exc()


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


@app.get("/api/experiment")
async def experiment(day: int = None):
    """Is this run's evidence admissible, and on which days? (v3.8)

    THE RULE THIS EXISTS FOR: do not open a window until clean_days() has
    returned at least one day. Window 3 was opened without checking, ran
    sixteen days at ~84% live because one villager could not fit on a
    graphics card, and was void from the first tick. Nobody could tell,
    because reading the ledger meant sitting at the machine with a Python
    prompt — so nobody read it.

    An integrity line you have to walk across the room for is an integrity
    line nobody checks. (claude/WINDOW3-VOID.md)
    """
    exp = getattr(engine, "exp", None)
    if exp is None or not getattr(exp, "run", None):
        return {"run": None, "note": "no run open yet — the ledger opens "
                                     "on the first tick"}
    run = exp.run
    clean = exp.clean_days()
    out = {
        "run_id": run.get("run_id"),
        "town": run.get("town"),
        "code_version": run.get("code_version"),
        "seed": run.get("seed"),
        "mock_mode": run.get("mock_mode"),
        "opened_day": run.get("opened_day"),
        "ended_reason": run.get("ended_reason"),
        "interventions": len(run.get("interventions") or []),
        "integrity": exp.integrity(),
        "clean_days": clean,
        "clean_day_count": len(clean),
        "today": exp.day_integrity(engine.world.clock.day),
        "admissible": bool(clean),
    }
    if day is not None:
        out["day_requested"] = exp.day_integrity(day)
    return out


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
        "chaos": sorted(m[4:] for m in dir(type(engine.director))
                        if m.startswith("_ev_")),
    }


@app.post("/api/chaos")
async def chaos(body: dict = None):
    """Manually fire a Director event. Body: {"event": "stranger"} or empty
    for a random weighted roll. You are the god of this town; use it wisely."""
    name = (body or {}).get("event")
    # Validate against the Director's actual handlers, not the config's
    # weight table — an old town config predates newer events (the heist
    # taught us this: weight-0 god-button events aren't in old configs,
    # but the code can still fire them). Config weights only steer the
    # random roll; the god button answers to the code.
    known = {m[4:] for m in dir(type(engine.director))
             if m.startswith("_ev_")}
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

from contextvars import ContextVar

actor_ctx = ContextVar("actor_ctx", default={
    "executed_by_type": "unknown",
    "user": None,
    "actor": None,
    "origin": "unknown",
    "display": "unknown",
})

def as_user(username: str, origin="ui:request"):
    global actor_ctx
    actor_ctx.set({
        "executed_by_type": "user",
        "user": username,
        "actor": None,
        "origin": origin,
        "display": username,
    })

def as_agent_for(username: str, actor: str = "system", origin="unknown"):
    global actor_ctx
    actor_ctx.set({
        "executed_by_type": "user+agent",
        "user": username,
        "actor": actor,
        "origin": origin,
        "display": f"{username}/{actor}"
    })

def as_system(origin: str = "unknown", actor: str = "system"):
    global actor_ctx
    actor_ctx.set({
        "executed_by_type": "agent",
        "user": None,
        "actor": actor,
        "origin": origin,
        "display": f"/{actor}"
    })

def overlay_actor(origin: str = "unknown", actor: str = "system"):
    global actor_ctx
    actor_ctx["executed_by_type"] = "user+agent" if actor_ctx["user"] else "agent"
    actor_ctx["actor"] = actor
    actor_ctx["origin"] = origin
    username = actor_ctx["user"] if actor_ctx["user"] else ""
    actor_ctx["display"] = f"{username}/{actor}"

def get_actor_ctx():
    global actor_ctx
    return actor_ctx

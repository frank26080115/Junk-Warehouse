from contextvars import ContextVar

from typing import Dict, Optional

# The default context captures every field that callers expect to read.
# Each helper function works with copies so the shared ContextVar default
# stays immutable and predictable across threads and async tasks.
_DEFAULT_CONTEXT: Dict[str, Optional[str]] = {
    "executed_by_type": "unknown",
    "user": None,
    "actor": None,
    "origin": "unknown",
    "display": None,
}

# The ContextVar stores the most recent actor information for the current execution context.
actor_ctx: ContextVar[Dict[str, Optional[str]]] = ContextVar(
    "actor_ctx",
    default=_DEFAULT_CONTEXT.copy(),
)

def _build_default_context() -> Dict[str, Optional[str]]:
    """Return a fresh dictionary containing the canonical context keys."""
    return _DEFAULT_CONTEXT.copy()

def _replace_context(overrides: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """Replace the entire context using defaults plus the supplied overrides."""
    fresh = _build_default_context()
    fresh.update(overrides)
    actor_ctx.set(fresh)
    return fresh

def _ensure_context() -> Dict[str, Optional[str]]:
    """Guarantee that the ContextVar holds every expected key with safe defaults."""
    current = actor_ctx.get()
    if isinstance(current, dict):
        normalized = _build_default_context()
        normalized.update(current)
        # Refresh the ContextVar so future reads observe the normalized structure.
        actor_ctx.set(normalized)
        return normalized
    return _replace_context({})

def _merge_context(overrides: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """Update the current context while preserving unspecified values."""
    base = _ensure_context()
    merged = dict(base)
    merged.update(overrides)
    actor_ctx.set(merged)
    return merged

def as_user(username: str, origin: str = "ui:request") -> Dict[str, Optional[str]]:
    """Record that a human user initiated the current action."""
    return _replace_context({
        "executed_by_type": "user",
        "user": username,
        "actor": None,
        "origin": origin,
        "display": username,
    })

def as_agent_for(username: str, actor: str = "system", origin: str = "unknown") -> Dict[str, Optional[str]]:
    """Describe work performed by an automated actor on behalf of a user."""
    return _replace_context({
        "executed_by_type": "user+agent",
        "user": username,
        "actor": actor,
        "origin": origin,
        "display": f"{username}/{actor}",
    })

def as_system(origin: str = "unknown", actor: str = "system") -> Dict[str, Optional[str]]:
    """Capture a context for fully automated system activity."""
    return _replace_context({
        "executed_by_type": "agent",
        "user": None,
        "actor": actor,
        "origin": origin,
        "display": f"/{actor}",
    })

def overlay_actor(origin: str = "unknown", actor: str = "system") -> Dict[str, Optional[str]]:
    """Augment the current context with an additional actor while keeping the user."""
    base = _ensure_context()
    username = base.get("user") or ""
    executed_by_type = "user+agent" if username else "agent"
    display_value = f"{username}/{actor}" if username else f"/{actor}"
    return _merge_context({
        "executed_by_type": executed_by_type,
        "actor": actor,
        "origin": origin,
        "display": display_value,
    })

def get_actor_ctx() -> Dict[str, Optional[str]]:
    """Return a copy of the current actor context for read-only callers."""
    return dict(_ensure_context())

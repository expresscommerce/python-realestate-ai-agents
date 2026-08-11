"""Session management: Redis with in-memory fallback.

Stores chat history, search state, and listing state per session_id.
Redis provides persistence across restarts; in-memory dict is the
fallback when Redis is unavailable.
"""

import copy
import json
import logging

from .config import REDIS_URL
from .listing_state import DEFAULT_LISTING_STATE
from .search_state import DEFAULT_SEARCH_STATE

log = logging.getLogger(__name__)

_local: dict[str, list[dict]] = {}
_local_state: dict = {}
_local_listing: dict = {}
_redis = None

SESSION_TTL = 60 * 60 * 24  # 24 hours


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        return None
    try:
        import redis as redis_lib
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        _redis.ping()
        log.info("Redis connected")
        return _redis
    except Exception as exc:
        log.warning("Redis unavailable, using in-memory sessions: %s", exc)
        _redis = False  # type: ignore[assignment]
        return None


# ── Chat History ──

def get_history(session_id: str) -> list[dict]:
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"sess:{session_id}")
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return list(_local.get(session_id, []))


def save_history(session_id: str, history) -> None:
    _local[session_id] = history
    r = _get_redis()
    if r:
        try:
            r.setex(f"sess:{session_id}", SESSION_TTL, json.dumps(history))
        except Exception:
            pass


# ── Search State ──

def get_search_state(session_id):
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"state:{session_id}")
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return _local_state.get(session_id, DEFAULT_SEARCH_STATE.copy())


def save_search_state(session_id, state):
    _local_state[session_id] = state
    r = _get_redis()
    if r:
        try:
            r.setex(f"state:{session_id}", SESSION_TTL, json.dumps(state))
        except Exception:
            pass


# ── Listing State ──

def get_listing_state(session_id):
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"listing:{session_id}")
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return _local_listing.get(session_id) or copy.deepcopy(DEFAULT_LISTING_STATE)


def save_listing_state(session_id, state):
    _local_listing[session_id] = state
    r = _get_redis()
    if r:
        try:
            r.setex(f"listing:{session_id}", SESSION_TTL, json.dumps(state))
        except Exception:
            pass


# ── Cleanup ──

def clear_history(session_id: str) -> None:
    _local.pop(session_id, None)
    _local_state.pop(session_id, None)
    _local_listing.pop(session_id, None)
    r = _get_redis()
    if r:
        try:
            r.delete(f"sess:{session_id}")
            r.delete(f"state:{session_id}")
            r.delete(f"listing:{session_id}")
        except Exception:
            pass

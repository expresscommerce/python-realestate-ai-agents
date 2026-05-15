"""Session management: Redis with in-memory fallback.

Stores chat history per session_id. Redis provides persistence across
restarts; in-memory dict is the fallback when Redis is unavailable.
"""

import json
import logging

from .config import REDIS_URL

log = logging.getLogger(__name__)

_local: dict[str, list[dict]] = {}
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


def save_history(session_id: str, history: list[dict]) -> None:
    _local[session_id] = history
    r = _get_redis()
    if r:
        try:
            r.setex(f"sess:{session_id}", SESSION_TTL, json.dumps(history))
        except Exception:
            pass


def clear_history(session_id: str) -> None:
    _local.pop(session_id, None)
    r = _get_redis()
    if r:
        try:
            r.delete(f"sess:{session_id}")
        except Exception:
            pass

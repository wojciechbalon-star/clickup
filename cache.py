import json
import time
from pathlib import Path
from typing import Optional

CACHE_FILE = Path("cache.json")
CACHE_TTL = 15 * 60  # seconds


def load_cache() -> Optional[dict]:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data["timestamp"] > CACHE_TTL:
            return None
        return data["payload"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_cache(payload: dict) -> None:
    CACHE_FILE.write_text(json.dumps({
        "timestamp": time.time(),
        "payload": payload,
    }))


def clear_cache() -> None:
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()

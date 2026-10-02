"""L1 in-process cache (LRU + TTL) with hit/miss statistics.

L1 hits are served in single-digit milliseconds; that is what the sub-15ms
cache-hit evidence in the report and demo video relies on. The shared
DynamoDB-backed L2 tier (survives container churn, shared by every warm
Lambda container) lives in store.py / rag.py.
"""

import threading
import time
from collections import OrderedDict


class L1Cache:
    def __init__(self, capacity: int = 512, default_ttl: int = 3600):
        self.capacity = capacity
        self.default_ttl = default_ttl
        self._data = OrderedDict()  # key -> (expires_at, value, stored_at)
        self._lock = threading.Lock()
        self.statsd = {"hits": 0, "misses": 0, "sets": 0, "evictions": 0, "expired": 0}

    def get(self, key):
        """Return (value, age_ms) on a fresh hit, else None. Counts hits/misses."""
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.statsd["misses"] += 1
                return None
            expires, value, stored_at = item
            if expires < now:
                del self._data[key]
                self.statsd["expired"] += 1
                self.statsd["misses"] += 1
                return None
            self._data.move_to_end(key)
            self.statsd["hits"] += 1
            return value, int((now - stored_at) * 1000)

    def set(self, key, value, ttl: int = None):
        now = time.time()
        with self._lock:
            self._data[key] = (now + (ttl if ttl is not None else self.default_ttl), value, now)
            self._data.move_to_end(key)
            self.statsd["sets"] += 1
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)
                self.statsd["evictions"] += 1

    def purge(self, prefix: str = "") -> int:
        with self._lock:
            if not prefix:
                n = len(self._data)
                self._data.clear()
                return n
            victims = [k for k in self._data if k.startswith(prefix)]
            for k in victims:
                del self._data[k]
            return len(victims)

    def stats(self) -> dict:
        with self._lock:
            total = self.statsd["hits"] + self.statsd["misses"]
            return {
                "capacity": self.capacity,
                "entries": len(self._data),
                "hits": self.statsd["hits"],
                "misses": self.statsd["misses"],
                "sets": self.statsd["sets"],
                "evictions": self.statsd["evictions"],
                "expired": self.statsd["expired"],
                "hitRate": round(self.statsd["hits"] / total, 4) if total else 0.0,
            }


_l1 = None


def get_l1() -> L1Cache:
    global _l1
    if _l1 is None:
        from config import ANSWER_CACHE_TTL, L1_CAPACITY
        _l1 = L1Cache(capacity=L1_CAPACITY, default_ttl=ANSWER_CACHE_TTL)
    return _l1

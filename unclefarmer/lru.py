"""Bounded least-recently-used caches on plain dicts (the route, travel and plan caches).

A dict keeps insertion order, so the oldest entry is the first one: a hit moves its entry to the
end, and a full cache drops the front. dict.popitem() drops the END - the newest entry - so the
caches this replaces froze on whatever they held when they first filled, and every later question
was a miss. Plain dicts measured faster than cachebox's LRUCache on the hit path (benchmark.py).

Each step is one dict operation, which the GIL makes atomic, so two threads using one cache can
at worst both compute a missing answer: the answers depend only on the key, never on which
thread got there first.
"""

_MISSING = object()


def get(cache, key, default=None):
    """cache[key], now the most recently used, or default."""
    value = cache.pop(key, _MISSING)
    if value is _MISSING:
        return default
    cache[key] = value
    return value


def put(cache, key, value, most):
    """cache[key] = value, dropping the least recently used entries past most."""
    cache[key] = value
    while len(cache) > most:
        try:
            del cache[next(iter(cache))]
        except (KeyError, RuntimeError, StopIteration):
            break                 # another thread changed it under us: it is bounded next time

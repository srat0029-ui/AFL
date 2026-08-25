"""Per-request memoization for expensive, request-invariant computations
(Current Context + Team News Intelligence stage's performance follow-up —
the user's own explicit ask: fix the Weekly Review page's ~21s load by
caching rather than recomputing per request).

Profiling `build_weekly_review_page` showed `load_normalized_prop_insights`
(~3s/call) and `build_model_context` (~0.6s/call) each being called 4-5
times PER PAGE LOAD — every hierarchy section (Final Shortlist, Strongest
Player/Team Opportunities, Markets Waiting on Confirmation, Model vs
Market Disagreements, Weekly Summary) independently recomputes the exact
same result from the exact same DB state, because each of those existing
modules is designed to be callable standalone (Prop Insights page, Best
Opportunities page, tests) and has no way to know a sibling call in the
same request already did the work.

Keyed by the SQLAlchemy Session object itself via a WeakKeyDictionary: a
cache entry lives exactly as long as the request's Session does (FastAPI's
`get_db` creates a fresh Session per request and closes it after), so this
can never leak stale data across requests or grow unbounded, and needs no
explicit invalidation — closing/garbage-collecting the session drops its
entry automatically. Every existing call site benefits with zero signature
changes, since the cache lives inside the wrapped functions themselves,
not in each caller.
"""

import time
import uuid
import weakref
from typing import Callable, TypeVar

T = TypeVar("T")

_caches: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def cached(db, key: tuple, compute: Callable[[], T]) -> T:
    session_cache = _caches.get(db)
    if session_cache is None:
        session_cache = {}
        _caches[db] = session_cache
    if key not in session_cache:
        session_cache[key] = compute()
    return session_cache[key]


# --- Short-TTL cross-request cache -----------------------------------------
#
# `cached()` above only helps within ONE request's Session — a real repeat
# visit to the Weekly Review page gets a brand-new Session per request (see
# app/database.py's get_db), so it never benefits. Profiling showed the
# single largest remaining cost even on a warm server (~8.7s of the page's
# original ~21-26s) was load_normalized_prop_insights's own query/compute
# pass, independently repeated on every request regardless of session.
#
# The underlying PlayerPropMarket/projection data this reads only actually
# changes when a live-cycle refresh runs (odds/prop-odds ingestion,
# project-upcoming) - a periodic, human-or-cron-triggered event, not a
# continuous stream - so a short, disclosed TTL is a safe way to persist
# this across requests without risking meaningfully stale numbers on a
# "review before deciding" page. Process-global (not session-keyed): the
# result doesn't depend on which Session object read it, only on the
# underlying DB state.
#
# Every real data-changing path (live_cycle.py, the CLI ingestion/backfill/
# pricing commands, and every lineup-edit/projection-regen route) already
# calls clear_ttl_cache() explicitly the moment it changes anything - so
# this TTL is only ever a safety-net upper bound, not the actual freshness
# mechanism. With real finals-scale data this cache's own compute pass costs
# ~16-20s, so the original 60s window meant any two page loads more than a
# minute apart (a completely normal browsing pace) paid that cost again;
# raised to 10 minutes so a realistic browsing session mostly stays on the
# fast path, while still bounding worst-case staleness if an invalidation
# were ever missed.
_ttl_cache: dict[tuple, tuple[float, object]] = {}
_engine_tokens: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
DEFAULT_TTL_SECONDS = 600.0


def _engine_token(db) -> str:
    """A stable, collision-free identifier for the Session's underlying
    Engine — NOT raw id(), which Python can and does reuse once an object
    is garbage-collected (a real risk here: the test suite creates and
    disposes a fresh Engine per test in well under a second)."""
    engine = db.get_bind()
    token = _engine_tokens.get(engine)
    if token is None:
        token = uuid.uuid4().hex
        _engine_tokens[engine] = token
    return token


def cached_with_ttl(db, key: tuple, compute: Callable[[], T], ttl_seconds: float = DEFAULT_TTL_SECONDS, *, store: dict | None = None) -> T:
    """Scoped by the Session's underlying Engine, not just `key` — this app
    runs one persistent Engine for the life of the real server process (so
    production correctly gets cross-request caching), but the test suite
    creates a brand-new isolated in-memory Engine per test via its own
    db_session fixture. Keying on `key` alone let one test's cached result
    leak into a completely different test's assertions the moment both
    called this with identical arguments — caught by a full test-suite run
    immediately after this cache was introduced.

    `store` lets a caller use a private dict instead of the shared
    `_ttl_cache` (see `cached_model_fit` below) so that clearing one cache
    doesn't also evict entries that a different invalidation policy owns."""
    if store is None:
        store = _ttl_cache
    full_key = (_engine_token(db), key)
    now = time.monotonic()
    entry = store.get(full_key)
    if entry is not None:
        expires_at, value = entry
        if now < expires_at:
            return value
    value = compute()
    store[full_key] = (now + ttl_seconds, value)
    return value


def clear_ttl_cache() -> None:
    """Exposed for tests and for CLI refresh commands that want the very
    next page load to reflect a just-completed refresh immediately rather
    than waiting out the TTL."""
    _ttl_cache.clear()


# --- Live-projection model-fit cache ----------------------------------------
#
# `generate_live_projections` (live_engine.py) re-fits the disposal/goal
# regression models against the full historical PlayerMatchStat dataset
# (~100k rows) on every call — profiling a single-match lineup confirmation
# showed this costing ~70-80s, almost none of it actually specific to the
# match/lineup that changed. The fitted model and its training dataset only
# depend on historical stats + which model run is promoted, NOT on any
# match's current lineup, so they're cached separately from `_ttl_cache`
# here: lineup-edit endpoints call `clear_ttl_cache()` on every save (correct
# for lineup-dependent caches like prop insights) and must NOT also evict
# this one, or every save would still pay the full re-fit cost. Invalidated
# instead at the same points `clear_ttl_cache()` already is for a real data
# refresh or model promotion (ingestion CLI, live_cycle.py) via
# `clear_model_fit_cache()`, backed by a generous TTL as a safety net.
_model_fit_cache: dict[tuple, tuple[float, object]] = {}
MODEL_FIT_TTL_SECONDS = 900.0


def cached_model_fit(db, key: tuple, compute: Callable[[], T], ttl_seconds: float = MODEL_FIT_TTL_SECONDS) -> T:
    return cached_with_ttl(db, key, compute, ttl_seconds, store=_model_fit_cache)


def clear_model_fit_cache() -> None:
    _model_fit_cache.clear()

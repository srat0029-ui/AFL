"""External B2B API platform concerns — authentication, rate limiting,
request correlation, usage logging, and a unified error contract for
`/api/v1/pricing/*` and `/api/v1/market-intelligence/*`.

Deliberately its own package, mirroring `app.market_monitor`/
`app.trading_monitor`'s precedent of one dedicated package per cohesive
concern, and deliberately NOT protecting every route in this application —
internal product routes (`/api/afl/*`, `/api/v1/model-registry/*`,
`/api/v1/market-monitor/*`, `/api/v1/trading-monitor/*`, `/api/dashboard`,
...) are same-origin frontend consumption, not the external B2B surface,
and stay unauthenticated. See app/api/routes/pricing_v1.py for exactly
which routes use `Depends(require_api_key)`.
"""

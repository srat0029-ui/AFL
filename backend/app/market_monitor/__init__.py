"""B2B AFL Market Anomaly / Trading QA Engine.

An independent, neutral second set of eyes on this engine's own pricing
versus bookmaker markets — for a sportsbook, betting-tech, or sports-data
company's trading QA workflow, not a consumer "find me a bet" surface. See
app/market_monitor/types.py for the alert vocabulary and app/market_monitor/
detector.py for the orchestration entry point.

Read-only with respect to pricing: nothing here ever writes to a
PlayerDisposalProjection/PlayerGoalProjection/TeamMarketPrice, changes a
model probability, or feeds the consumer Multi Builder. It only reads
already-computed prices (app/pricing/*) and already-recorded market/context
data and reports discrepancies, using neutral, non-accusatory language —
see types.py's ALERT_DESCRIPTIONS for the exact wording convention (never
"the bookmaker is wrong").
"""

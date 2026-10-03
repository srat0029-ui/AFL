"""Expected Minutes Model V1: predicts how many minutes an NBA player plays in
a game (given that he plays), from information available before tip-off.

V1 uses ONLY historically reconstructable information - prior box scores and
the schedule. Prospective injury/availability evidence is deliberately left
out so that a later V2 (V1 + that evidence) measures what the evidence adds.

Modules:
- data:       loads games and box scores from the database into frames
- features:   the leakage-safe feature builder (as-of joins on a time cutoff)
- dataset:    historical training rows and eligibility rules
- models:     baselines and model candidates
- metrics:    point-error metrics and breakdowns
- evaluation: chronological season holdout and walk-forward evaluation
- residuals:  error-distribution analysis and the V1 uncertainty table
- registry:   append-only model-run records and fitted-model files
- prospective: frozen predictions for upcoming games
- cli:        `python -m app.nba.minutes.cli ...`
"""

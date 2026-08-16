from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    afl,
    backtest,
    backtests,
    dashboard,
    edges,
    goal_models,
    health,
    matches,
    odds,
    player_models,
    player_projections,
    predictions,
)
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AFL Analytics API",
    description="Analytics and betting-insight API for the AFL analytics platform.",
    version="0.1.0",
)

if settings.app_env == "local":
    # Local dev servers (Vite, this launcher, etc.) can get reassigned to a
    # different port whenever their default is busy, so pin to "any localhost
    # port" instead of the single port in CORS_ORIGINS. Non-local environments
    # still use the explicit allowlist below.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router)
app.include_router(matches.router)
app.include_router(afl.router)
app.include_router(odds.odds_router)
app.include_router(odds.delete_router)
app.include_router(odds.bookmakers_router)
app.include_router(edges.router)
app.include_router(predictions.router)
app.include_router(dashboard.router)
app.include_router(backtest.router)
app.include_router(backtests.router)
app.include_router(player_models.router)
app.include_router(goal_models.router)
app.include_router(player_projections.router)

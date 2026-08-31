from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    afl,
    backtest,
    backtests,
    context,
    dashboard,
    edges,
    goal_models,
    health,
    live_status,
    market_monitor_v1,
    matches,
    model_registry_v1,
    odds,
    placed_bets,
    player_identity,
    player_models,
    player_projections,
    predictions,
    pricing_v1,
    real_market_tracking,
    refresh,
    trading_monitor_v1,
    weekly_review,
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
app.include_router(real_market_tracking.router)
app.include_router(player_identity.router)
app.include_router(live_status.router)
app.include_router(refresh.router)
app.include_router(weekly_review.router)
app.include_router(context.router)
app.include_router(placed_bets.router)
app.include_router(pricing_v1.pricing_router)
app.include_router(pricing_v1.market_intel_router)
app.include_router(pricing_v1.integration_router)
app.include_router(model_registry_v1.router)
app.include_router(market_monitor_v1.router)
app.include_router(trading_monitor_v1.router)

"""Synthetic NBA rows for tests. Deliberately generic names ("Home Team",
"Test Player") — these are fixtures for exercising the pipeline's rules, not
representations of real NBA teams, players, or prices."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.models.nba import NbaGame, NbaGameStatus, NbaPlayer, NbaPlayerGameLog, NbaPropQuote, NbaTeam
from app.nba.markets import NbaPropMarket
from app.nba.projection import ProjectionOutput

TIPOFF = datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)


class FixedDistribution:
    """A StatDistribution over an explicit {integer outcome: probability} table."""

    def __init__(self, pmf: dict[int, float]):
        self._pmf = pmf

    def mean(self) -> float:
        return sum(k * p for k, p in self._pmf.items())

    def prob_over(self, line: float) -> float:
        return sum(p for k, p in self._pmf.items() if k > line)

    def prob_under(self, line: float) -> float:
        return sum(p for k, p in self._pmf.items() if k < line)


# P(over 24.5) = 0.6, P(under 24.5) = 0.4; on the whole-number line 25, P(push) = 0.2.
POINTS_DISTRIBUTION = FixedDistribution({20: 0.4, 25: 0.2, 30: 0.4})


def projection_output(market: NbaPropMarket = NbaPropMarket.POINTS, distribution=POINTS_DISTRIBUTION) -> ProjectionOutput:
    return ProjectionOutput(
        market=market,
        distribution=distribution,
        distribution_kind="fixed_test",
        distribution_params={},
        games_of_history=10,
        expected_minutes=32.0,
        rate_per_minute=distribution.mean() / 32.0,
    )


def seed_game(db, *, tipoff: datetime = TIPOFF, status: NbaGameStatus = NbaGameStatus.SCHEDULED, source_game_id: str = "g1"):
    home = db.query(NbaTeam).filter_by(name="Home Team").one_or_none() or NbaTeam(name="Home Team", abbreviation="HOM")
    away = db.query(NbaTeam).filter_by(name="Away Team").one_or_none() or NbaTeam(name="Away Team", abbreviation="AWY")
    db.add_all([home, away])
    db.flush()
    game = NbaGame(
        season_start_year=2026, game_date=(tipoff - timedelta(hours=8)).date(), scheduled_start=tipoff, status=status.value,
        home_team_id=home.id, away_team_id=away.id, source="test", source_game_id=source_game_id,
    )
    db.add(game)
    db.flush()
    return game, home, away


def seed_player(db, team: NbaTeam, name: str = "Test Player", source_player_id: str = "p1") -> NbaPlayer:
    player = NbaPlayer(display_name=name, current_team_id=team.id, source="test", source_player_id=source_player_id)
    db.add(player)
    db.flush()
    return player


def seed_bookmaker(db, name: str = "Book A", eligibility: str = ELIGIBILITY_INCLUDED) -> Bookmaker:
    bookmaker = Bookmaker(name=name, eligibility=eligibility)
    db.add(bookmaker)
    db.flush()
    return bookmaker


def add_quote(
    db, *, game, player, bookmaker, observed_at: datetime, price: float, selection: str = "over", line: float = 24.5,
    market: NbaPropMarket = NbaPropMarket.POINTS, is_alternate_line: bool = False,
) -> NbaPropQuote:
    quote = NbaPropQuote(
        game_id=game.id, player_id=player.id, bookmaker_id=bookmaker.id, market=market.value, line=line, selection=selection,
        is_alternate_line=is_alternate_line, price_decimal=price, observed_at=observed_at, source="test",
    )
    db.add(quote)
    db.flush()
    return quote


def add_game_log(db, *, game, player, team, opponent, minutes: float | None = 30.0, points: int | None = None, did_not_play: bool = False, **stats) -> NbaPlayerGameLog:
    log = NbaPlayerGameLog(
        player_id=player.id, game_id=game.id, team_id=team.id, opponent_team_id=opponent.id, is_home=True, source="test",
        recorded_at=game.scheduled_start + timedelta(hours=5), minutes=minutes, points=points, did_not_play=did_not_play, **stats,
    )
    db.add(log)
    db.flush()
    return log

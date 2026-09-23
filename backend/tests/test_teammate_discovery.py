"""Tests for app/player_modelling/teammate_discovery.py - the teammate
candidate pool, per-candidate reuse of the exact player_context_analysis
split/adjustment/confidence methodology, and the evidence-first (never
effect-size-first) ordering.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.player_context_analysis import PlayerContextConfidenceTier
from app.player_modelling.teammate_discovery import build_teammate_discovery

BASE = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _seed_club(db, suffix=""):
    sport = Sport(code=f"AFL{suffix}", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2024)
    db.add(season)
    db.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id=f"players/S/Sheezel{suffix}.html")
    db.add(player)
    db.commit()
    return sport, season, home, away, player


def _add_match(db, sport, season, round_number, home, away, start):
    round_ = Round(season_id=season.id, round_number=round_number)
    db.add(round_)
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=start, status=MatchStatus.COMPLETED,
    )
    db.add(match)
    db.commit()
    return match


def _add_stat(db, *, player, match, team, opponent_team, disposals, goals=1, tog=85):
    row = PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=team.id, opponent_team_id=opponent_team.id,
        source="afltables", recorded_at=match.scheduled_start, disposals=disposals, goals=goals, time_on_ground_pct=tog,
    )
    db.add(row)
    db.commit()
    return row


def test_unknown_player_raises_lookup_error(db_session):
    with pytest.raises(LookupError):
        build_teammate_discovery(db_session, 999999)


def test_no_recorded_stats_returns_empty_candidates_not_an_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Rookie", source="afltables", source_player_id="players/R/Rookie.html")
    db_session.add(player)
    db_session.commit()

    discovery = build_teammate_discovery(db_session, player.id)
    assert discovery.candidates == []
    assert discovery.team_id is None
    assert "No recorded match statistics" in discovery.explanation


def test_player_with_games_but_no_teammates_recorded_returns_empty_candidates(db_session):
    sport, season, home, away, player = _seed_club(db_session)
    match = _add_match(db_session, sport, season, 1, home, away, BASE)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=20)

    discovery = build_teammate_discovery(db_session, player.id)
    assert discovery.candidates == []
    assert discovery.team_id == home.id
    assert "No other player" in discovery.explanation


def test_finds_every_distinct_teammate_with_at_least_one_shared_match(db_session):
    sport, season, home, away, player = _seed_club(db_session)
    teammate_a = Player(sport_id=sport.id, display_name="Teammate A", source="afltables", source_player_id="players/A/A.html")
    teammate_b = Player(sport_id=sport.id, display_name="Teammate B", source="afltables", source_player_id="players/B/B.html")
    db_session.add_all([teammate_a, teammate_b])
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(10):
        match = _add_match(db_session, sport, season, round_number, home, away, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=20 + i)
        if i < 8:  # teammate_a shares 8 games
            _add_stat(db_session, player=teammate_a, match=match, team=home, opponent_team=away, disposals=15)
        if i < 1:  # teammate_b shares only 1 game
            _add_stat(db_session, player=teammate_b, match=match, team=home, opponent_team=away, disposals=10)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_teammate_discovery(db_session, player.id)
    ids = {c.teammate_id for c in discovery.candidates}
    assert ids == {teammate_a.id, teammate_b.id}

    a = next(c for c in discovery.candidates if c.teammate_id == teammate_a.id)
    b = next(c for c in discovery.candidates if c.teammate_id == teammate_b.id)
    assert a.with_teammate.games == 8
    assert a.without_teammate.games == 2
    assert b.with_teammate.games == 1
    assert b.without_teammate.games == 9


def test_sufficient_evidence_flag_matches_confidence_tier(db_session):
    sport, season, home, away, player = _seed_club(db_session)
    well_evidenced = Player(sport_id=sport.id, display_name="Well Evidenced", source="afltables", source_player_id="players/W/W.html")
    barely_seen = Player(sport_id=sport.id, display_name="Barely Seen", source="afltables", source_player_id="players/X/X.html")
    db_session.add_all([well_evidenced, barely_seen])
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(20):
        match = _add_match(db_session, sport, season, round_number, home, away, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=20)
        if i < 10:
            _add_stat(db_session, player=well_evidenced, match=match, team=home, opponent_team=away, disposals=15)
        if i == 0:  # only ever shares a single game - below MIN_GAMES_INSUFFICIENT
            _add_stat(db_session, player=barely_seen, match=match, team=home, opponent_team=away, disposals=15)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_teammate_discovery(db_session, player.id)
    well = next(c for c in discovery.candidates if c.teammate_id == well_evidenced.id)
    barely = next(c for c in discovery.candidates if c.teammate_id == barely_seen.id)

    assert well.sufficient_evidence is True
    assert well.confidence.tier is not PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert barely.sufficient_evidence is False
    assert barely.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY


def test_ordering_is_evidence_first_never_effect_size_first(db_session):
    """A teammate with a huge raw/adjusted difference but tiny sample size
    must NOT outrank a teammate with solid evidence and a small difference.
    """
    sport, season, home, away, player = _seed_club(db_session)
    solid = Player(sport_id=sport.id, display_name="Solid Evidence", source="afltables", source_player_id="players/S2/S2.html")
    noisy = Player(sport_id=sport.id, display_name="Noisy Outlier", source="afltables", source_player_id="players/N/N.html")
    db_session.add_all([solid, noisy])
    db_session.commit()

    # `noisy` has an earlier same-club appearance, so the player's 20 earlier games are eligible "without" games for them.
    early = _add_match(db_session, sport, season, 0, home, away, BASE - timedelta(days=7))
    _add_stat(db_session, player=noisy, match=early, team=home, opponent_team=away, disposals=12)
    round_number = 1
    start = BASE
    # 20 games with `solid`: player's disposals barely differ in/out.
    for i in range(20):
        match = _add_match(db_session, sport, season, round_number, home, away, start)
        disposals = 20 if i % 2 == 0 else 21
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=disposals)
        if i % 2 == 0:
            _add_stat(db_session, player=solid, match=match, team=home, opponent_team=away, disposals=15)
        round_number += 1
        start += timedelta(days=7)
    # 1 extra game with `noisy`: a single wildly different value - a huge
    # raw difference but built on essentially no evidence.
    match = _add_match(db_session, sport, season, round_number, home, away, start)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=50)
    _add_stat(db_session, player=noisy, match=match, team=home, opponent_team=away, disposals=10)

    discovery = build_teammate_discovery(db_session, player.id)
    noisy_candidate = next(c for c in discovery.candidates if c.teammate_id == noisy.id)
    solid_candidate = next(c for c in discovery.candidates if c.teammate_id == solid.id)

    # Sanity check the scenario actually produced a large noisy effect.
    assert noisy_candidate.raw_difference is not None
    assert solid_candidate.raw_difference is not None

    solid_rank = discovery.candidates.index(solid_candidate)
    noisy_rank = discovery.candidates.index(noisy_candidate)
    assert solid_rank < noisy_rank
    assert solid_candidate.sufficient_evidence is True
    assert noisy_candidate.sufficient_evidence is False


def test_candidate_reuses_context_analysis_adjusted_effect_methodology(db_session):
    """The adjusted effect for a candidate must match exactly what
    build_player_context_analysis would compute for that same pair - no
    separate/looser calculation for discovery.
    """
    from app.player_modelling.player_context_analysis import build_player_context_analysis

    sport, season, home, away, player = _seed_club(db_session)
    teammate = Player(sport_id=sport.id, display_name="Direct Compare", source="afltables", source_player_id="players/D2/D2.html")
    db_session.add(teammate)
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(20):
        match = _add_match(db_session, sport, season, round_number, home, away, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=20 + (i % 5))
        if i < 12:
            _add_stat(db_session, player=teammate, match=match, team=home, opponent_team=away, disposals=15)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_teammate_discovery(db_session, player.id)
    candidate = next(c for c in discovery.candidates if c.teammate_id == teammate.id)
    direct = build_player_context_analysis(db_session, player.id, teammate.id)

    assert candidate.with_teammate.games == direct.with_teammate.games
    assert candidate.with_teammate.mean == direct.with_teammate.mean
    assert candidate.without_teammate.games == direct.without_teammate.games
    assert candidate.raw_difference == direct.raw_difference
    assert candidate.adjusted_effect.available == direct.adjusted_effect.available
    assert candidate.adjusted_effect.value == direct.adjusted_effect.value
    assert candidate.confidence.tier == direct.confidence.tier


def test_opponent_playing_same_match_is_never_counted_as_teammate(db_session):
    sport, season, home, away, player = _seed_club(db_session)
    opponent_player = Player(sport_id=sport.id, display_name="Rival", source="afltables", source_player_id="players/R2/R2.html")
    db_session.add(opponent_player)
    db_session.commit()

    match = _add_match(db_session, sport, season, 1, home, away, BASE)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=away, disposals=20)
    _add_stat(db_session, player=opponent_player, match=match, team=away, opponent_team=home, disposals=18)

    discovery = build_teammate_discovery(db_session, player.id)
    assert discovery.candidates == []


def test_restricted_to_players_most_recent_club(db_session):
    sport, season, home, away, player = _seed_club(db_session)
    old_club = Team(sport_id=sport.id, name="Western Bulldogs", short_name="WB")
    db_session.add(old_club)
    db_session.commit()
    old_teammate = Player(sport_id=sport.id, display_name="Old Teammate", source="afltables", source_player_id="players/O/O.html")
    new_teammate = Player(sport_id=sport.id, display_name="New Teammate", source="afltables", source_player_id="players/T2/T2.html")
    db_session.add_all([old_teammate, new_teammate])
    db_session.commit()

    m_old = _add_match(db_session, sport, season, 1, old_club, away, BASE)
    _add_stat(db_session, player=player, match=m_old, team=old_club, opponent_team=away, disposals=10)
    _add_stat(db_session, player=old_teammate, match=m_old, team=old_club, opponent_team=away, disposals=12)

    m_new = _add_match(db_session, sport, season, 2, home, away, BASE + timedelta(days=200))
    _add_stat(db_session, player=player, match=m_new, team=home, opponent_team=away, disposals=25)
    _add_stat(db_session, player=new_teammate, match=m_new, team=home, opponent_team=away, disposals=15)

    discovery = build_teammate_discovery(db_session, player.id)
    ids = {c.teammate_id for c in discovery.candidates}
    assert ids == {new_teammate.id}
    assert discovery.team_id == home.id

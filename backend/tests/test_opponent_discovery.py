"""Tests for app/player_modelling/opponent_discovery.py - the opponent
candidate pool, per-candidate reuse of the exact opponent_context_analysis
split/adjustment/confidence methodology, and the evidence-first (never
effect-size-first) ordering.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.opponent_context_analysis import PlayerContextConfidenceTier, build_opponent_context_analysis
from app.player_modelling.opponent_discovery import build_opponent_discovery

BASE = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _seed_club(db, suffix=""):
    sport = Sport(code=f"AFL{suffix}", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2024)
    db.add(season)
    db.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    db.add(home)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id=f"players/S/Sheezel{suffix}.html")
    db.add(player)
    db.commit()
    return sport, season, home, player


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
        player_id=player.id, match_id=match.id, team_id=team.id,
        opponent_team_id=opponent_team.id if opponent_team is not None else None,
        source="afltables", recorded_at=match.scheduled_start, disposals=disposals, goals=goals, time_on_ground_pct=tog,
    )
    db.add(row)
    db.commit()
    return row


def test_unknown_player_raises_lookup_error(db_session):
    with pytest.raises(LookupError):
        build_opponent_discovery(db_session, 999999)


def test_no_recorded_stats_returns_empty_candidates_not_an_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Rookie", source="afltables", source_player_id="players/R/Rookie.html")
    db_session.add(player)
    db_session.commit()

    discovery = build_opponent_discovery(db_session, player.id)
    assert discovery.candidates == []
    assert discovery.team_id is None
    assert "No recorded match statistics" in discovery.explanation


def test_player_with_games_but_no_opponent_recorded_returns_empty_candidates(db_session):
    sport, season, home, player = _seed_club(db_session)
    opp = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add(opp)
    db_session.commit()
    match = _add_match(db_session, sport, season, 1, home, opp, BASE)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=None, disposals=20)

    discovery = build_opponent_discovery(db_session, player.id)
    assert discovery.candidates == []
    assert discovery.team_id == home.id
    assert "No recorded opponent information" in discovery.explanation


def test_finds_every_distinct_opponent_faced(db_session):
    sport, season, home, player = _seed_club(db_session)
    opp_a = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    opp_b = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db_session.add_all([opp_a, opp_b])
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(10):
        opponent = opp_a if i < 8 else opp_b  # 8 games vs opp_a, 2 vs opp_b
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=20 + i)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_opponent_discovery(db_session, player.id)
    ids = {c.opponent_team_id for c in discovery.candidates}
    assert ids == {opp_a.id, opp_b.id}

    a = next(c for c in discovery.candidates if c.opponent_team_id == opp_a.id)
    b = next(c for c in discovery.candidates if c.opponent_team_id == opp_b.id)
    assert a.against_opponent.games == 8
    assert a.against_other_opponents.games == 2
    assert b.against_opponent.games == 2
    assert b.against_other_opponents.games == 8


def test_sufficient_evidence_flag_matches_confidence_tier(db_session):
    sport, season, home, player = _seed_club(db_session)
    well_faced = Team(sport_id=sport.id, name="Well Faced", short_name="WFD")
    barely_faced = Team(sport_id=sport.id, name="Barely Faced", short_name="BFD")
    # A third opponent so `well_faced`'s "other opponents" bucket isn't
    # constrained down to `barely_faced`'s single game too - otherwise the
    # smaller-group rule would make even the well-evidenced candidate
    # insufficient, which would defeat the point of this test.
    padding = Team(sport_id=sport.id, name="Padding Opponent", short_name="PAD")
    db_session.add_all([well_faced, barely_faced, padding])
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(21):
        if i < 10:
            opponent = well_faced
        elif i == 10:
            opponent = barely_faced  # only ever faced once - below MIN_GAMES_INSUFFICIENT
        else:
            opponent = padding
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=20)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_opponent_discovery(db_session, player.id)
    well = next(c for c in discovery.candidates if c.opponent_team_id == well_faced.id)
    barely = next(c for c in discovery.candidates if c.opponent_team_id == barely_faced.id)

    assert well.sufficient_evidence is True
    assert well.confidence.tier is not PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert barely.sufficient_evidence is False
    assert barely.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY


def test_ordering_is_evidence_first_never_effect_size_first(db_session):
    """An opponent with a huge raw/adjusted difference but tiny sample
    size must NOT outrank an opponent with solid evidence and a small
    difference.
    """
    sport, season, home, player = _seed_club(db_session)
    solid = Team(sport_id=sport.id, name="Solid Evidence", short_name="SLD")
    noisy = Team(sport_id=sport.id, name="Noisy Outlier", short_name="NSY")
    # A third opponent so `solid`'s "other opponents" bucket isn't
    # constrained down to `noisy`'s single game alone - see the
    # equivalent note in test_sufficient_evidence_flag_matches_confidence_tier.
    padding = Team(sport_id=sport.id, name="Padding Opponent", short_name="PAD")
    db_session.add_all([solid, noisy, padding])
    db_session.commit()

    round_number = 1
    start = BASE
    # 20 games vs `solid`: disposals barely differ each game.
    for i in range(20):
        disposals = 20 if i % 2 == 0 else 21
        match = _add_match(db_session, sport, season, round_number, home, solid, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=solid, disposals=disposals)
        round_number += 1
        start += timedelta(days=7)
    # 10 games vs `padding` at a stable value - pads out `solid`'s "other
    # opponents" group without affecting the `noisy` comparison below.
    for _ in range(10):
        match = _add_match(db_session, sport, season, round_number, home, padding, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=padding, disposals=20)
        round_number += 1
        start += timedelta(days=7)
    # 1 game vs `noisy`: a single wildly different value - a huge raw
    # difference but built on essentially no evidence.
    match = _add_match(db_session, sport, season, round_number, home, noisy, start)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=noisy, disposals=50)

    discovery = build_opponent_discovery(db_session, player.id)
    noisy_candidate = next(c for c in discovery.candidates if c.opponent_team_id == noisy.id)
    solid_candidate = next(c for c in discovery.candidates if c.opponent_team_id == solid.id)

    assert noisy_candidate.raw_difference is not None
    assert solid_candidate.raw_difference is not None

    solid_rank = discovery.candidates.index(solid_candidate)
    noisy_rank = discovery.candidates.index(noisy_candidate)
    assert solid_rank < noisy_rank
    assert solid_candidate.sufficient_evidence is True
    assert noisy_candidate.sufficient_evidence is False


def test_candidate_reuses_context_analysis_adjusted_effect_methodology(db_session):
    """The adjusted effect for a candidate must match exactly what
    build_opponent_context_analysis would compute for that same opponent
    - no separate/looser calculation for discovery.
    """
    sport, season, home, player = _seed_club(db_session)
    opponent = Team(sport_id=sport.id, name="Direct Compare", short_name="DCM")
    other = Team(sport_id=sport.id, name="Other Side", short_name="OTH")
    db_session.add_all([opponent, other])
    db_session.commit()

    round_number = 1
    start = BASE
    for i in range(20):
        chosen = opponent if i < 12 else other
        match = _add_match(db_session, sport, season, round_number, home, chosen, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=chosen, disposals=20 + (i % 5))
        round_number += 1
        start += timedelta(days=7)

    discovery = build_opponent_discovery(db_session, player.id)
    candidate = next(c for c in discovery.candidates if c.opponent_team_id == opponent.id)
    direct = build_opponent_context_analysis(db_session, player.id, opponent.id)

    assert candidate.against_opponent.games == direct.against_opponent.games
    assert candidate.against_opponent.mean == direct.against_opponent.mean
    assert candidate.against_other_opponents.games == direct.against_other_opponents.games
    assert candidate.raw_difference == direct.raw_difference
    assert candidate.adjusted_effect.available == direct.adjusted_effect.available
    assert candidate.adjusted_effect.value == direct.adjusted_effect.value
    assert candidate.confidence.tier == direct.confidence.tier


def test_restricted_to_players_most_recent_club(db_session):
    sport, season, home, player = _seed_club(db_session)
    old_club = Team(sport_id=sport.id, name="Western Bulldogs", short_name="WB")
    old_opponent = Team(sport_id=sport.id, name="Old Opponent", short_name="OLD")
    new_opponent = Team(sport_id=sport.id, name="New Opponent", short_name="NEW")
    db_session.add_all([old_club, old_opponent, new_opponent])
    db_session.commit()

    m_old = _add_match(db_session, sport, season, 1, old_club, old_opponent, BASE)
    _add_stat(db_session, player=player, match=m_old, team=old_club, opponent_team=old_opponent, disposals=10)

    m_new = _add_match(db_session, sport, season, 2, home, new_opponent, BASE + timedelta(days=200))
    _add_stat(db_session, player=player, match=m_new, team=home, opponent_team=new_opponent, disposals=25)

    discovery = build_opponent_discovery(db_session, player.id)
    ids = {c.opponent_team_id for c in discovery.candidates}
    assert ids == {new_opponent.id}
    assert discovery.team_id == home.id


def test_opponent_unknown_rows_excluded_from_candidates_but_not_from_baseline(db_session):
    sport, season, home, player = _seed_club(db_session)
    opponent = Team(sport_id=sport.id, name="Known Opponent", short_name="KWN")
    db_session.add(opponent)
    db_session.commit()

    round_number = 1
    start = BASE
    for _ in range(5):
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=None, disposals=20)
        round_number += 1
        start += timedelta(days=7)
    for _ in range(5):
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=20)
        round_number += 1
        start += timedelta(days=7)

    discovery = build_opponent_discovery(db_session, player.id)
    # Only the known-opponent games form a candidate; the unknown-opponent
    # games contributed to trailing-form history but never became a
    # fabricated "opponent".
    assert len(discovery.candidates) == 1
    candidate = discovery.candidates[0]
    assert candidate.opponent_team_id == opponent.id
    assert candidate.against_opponent.games == 5
    assert candidate.against_other_opponents.games == 0

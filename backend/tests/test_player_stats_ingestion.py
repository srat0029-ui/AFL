from datetime import datetime, timezone

from sqlalchemy import select

from app.ingestion.player_stats import ingest_player_stats
from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.providers.types import PlayerStatLine


def _seed(db_session, home="Carlton", away="Richmond", round_number=1):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=round_number)
    home_team = Team(sport_id=sport.id, name=home, short_name=home[:3].upper())
    away_team = Team(sport_id=sport.id, name=away, short_name=away[:3].upper())
    db_session.add_all([round_, home_team, away_team])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home_team.id, away_team_id=away_team.id,
        scheduled_start=datetime(2024, 3, 14, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    db_session.add(match)
    db_session.commit()
    return {"sport": sport, "season": season, "match": match, "home": home_team, "away": away_team}


def _row(team, round_number=1, player_name="Acres, Blake", player_source_id="players/B/Blake_Acres.html", **stats) -> PlayerStatLine:
    return PlayerStatLine(
        sport_code="AFL",
        season_year=2024,
        round_number=round_number,
        team_name=team,
        player_name=player_name,
        player_source_id=player_source_id,
        recorded_at=datetime.now(timezone.utc),
        stats=stats,
    )


def test_ingest_creates_player_and_stat_row(db_session):
    seed = _seed(db_session)
    rows = [_row("Carlton", disposals=25, kicks=16)]

    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.players_created == 1
    assert result.stats_created == 1
    assert result.unmatched == []

    player = db_session.scalar(select(Player))
    assert player.display_name == "Blake Acres"
    assert player.source == "afltables"
    assert player.source_player_id == "players/B/Blake_Acres.html"
    assert player.current_team_id == seed["home"].id

    stat = db_session.scalar(select(PlayerMatchStat))
    assert stat.disposals == 25
    assert stat.kicks == 16
    assert stat.team_id == seed["home"].id
    assert stat.opponent_team_id == seed["away"].id
    assert stat.match_id == seed["match"].id


def test_ingest_is_idempotent_on_rerun(db_session):
    _seed(db_session)
    rows = [_row("Carlton", disposals=25)]

    ingest_player_stats(db_session, rows, season_year=2024)
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.players_created == 0
    assert result.stats_created == 0
    assert result.stats_updated == 0
    assert result.stats_unchanged == 1
    assert len(db_session.scalars(select(PlayerMatchStat)).all()) == 1
    assert len(db_session.scalars(select(Player)).all()) == 1


def test_ingest_updates_changed_values(db_session):
    _seed(db_session)
    ingest_player_stats(db_session, [_row("Carlton", disposals=25)], season_year=2024)
    result = ingest_player_stats(db_session, [_row("Carlton", disposals=30)], season_year=2024)

    assert result.stats_updated == 1
    stat = db_session.scalar(select(PlayerMatchStat))
    assert stat.disposals == 30


def test_same_source_player_id_never_creates_a_second_player_row(db_session):
    """The two-players-same-name scenario: AFL Tables disambiguates via the
    source id (e.g. a trailing digit), not the raw name — a genuinely
    different player must use a different source_player_id, and this
    ingestion must never merge or split based on name alone."""
    seed = _seed(db_session, round_number=1)
    _seed_round2 = Round(season_id=seed["season"].id, round_number=2)
    db_session.add(_seed_round2)
    db_session.flush()
    match2 = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=_seed_round2.id,
        home_team_id=seed["home"].id, away_team_id=seed["away"].id,
        scheduled_start=datetime(2024, 3, 21, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=88, away_score=70,
    )
    db_session.add(match2)
    db_session.commit()

    rows = [
        _row("Carlton", round_number=1, player_name="Carroll, Jack", player_source_id="players/J/Jack_Carroll.html", disposals=10),
        _row("Carlton", round_number=2, player_name="Carroll, Jack", player_source_id="players/J/Jack_Carroll1.html", disposals=15),
    ]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.players_created == 2  # two distinct real players sharing a name
    players = db_session.scalars(select(Player)).all()
    assert {p.source_player_id for p in players} == {"players/J/Jack_Carroll.html", "players/J/Jack_Carroll1.html"}


def test_player_current_team_updates_on_a_trade(db_session):
    """A player's PlayerMatchStat.team_id for an old match must NOT change
    when they're later traded — only Player.current_team_id (a display
    convenience) should move."""
    seed1 = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    # a second team + a later round, simulating the player having moved clubs
    geelong = Team(sport_id=seed1["sport"].id, name="Geelong", short_name="GEE")
    round10 = Round(season_id=seed1["season"].id, round_number=10)
    db_session.add_all([geelong, round10])
    db_session.flush()
    match2 = Match(
        sport_id=seed1["sport"].id, season_id=seed1["season"].id, round_id=round10.id,
        home_team_id=geelong.id, away_team_id=seed1["away"].id,
        scheduled_start=datetime(2024, 6, 1, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=100, away_score=60,
    )
    db_session.add(match2)
    db_session.commit()

    rows = [
        _row("Carlton", round_number=1, disposals=20),
        _row("Geelong", round_number=10, disposals=22),
    ]
    ingest_player_stats(db_session, rows, season_year=2024)

    player = db_session.scalar(select(Player))
    assert player.current_team_id == geelong.id  # most recently seen

    stats_by_match = {s.match_id: s for s in db_session.scalars(select(PlayerMatchStat)).all()}
    assert stats_by_match[seed1["match"].id].team_id == seed1["home"].id  # historical row unchanged
    assert stats_by_match[match2.id].team_id == geelong.id


def test_unknown_team_is_reported_not_silently_dropped(db_session):
    _seed(db_session)
    rows = [_row("Fitzroy", disposals=20)]

    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "Fitzroy" in result.unmatched[0]


def test_unknown_round_is_reported_not_silently_dropped(db_session):
    _seed(db_session)
    rows = [_row("Carlton", round_number=99, disposals=20)]

    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "99" in result.unmatched[0]


def test_missing_season_is_reported(db_session):
    _seed(db_session)
    rows = [_row("Carlton", disposals=20)]

    result = ingest_player_stats(db_session, rows, season_year=2099)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1


def test_team_that_did_not_play_that_round_is_reported_not_silently_dropped(db_session):
    """A row claiming a team played in a round where no match involving that
    team exists at all (0 candidates) must be reported, not guessed."""
    seed = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    geelong = Team(sport_id=seed["sport"].id, name="Geelong", short_name="GEE")
    db_session.add(geelong)
    db_session.commit()

    rows = [_row("Geelong", round_number=1, disposals=20)]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "Geelong" in result.unmatched[0]


def test_ambiguous_match_is_reported_not_guessed(db_session):
    """A team playing twice in the same round (shouldn't happen in real AFL
    data, but the ingestion must not silently pick one if it ever does)."""
    seed = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    geelong = Team(sport_id=seed["sport"].id, name="Geelong", short_name="GEE")
    db_session.add(geelong)
    db_session.flush()
    duplicate_match = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=seed["match"].round_id,
        home_team_id=seed["home"].id, away_team_id=geelong.id,
        scheduled_start=datetime(2024, 3, 14, 22, 0, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=50, away_score=40,
    )
    db_session.add(duplicate_match)
    db_session.commit()

    rows = [_row("Carlton", round_number=1, disposals=20)]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "ambiguous" in result.unmatched[0].lower()


def test_subs_and_jumper_number_persisted(db_session):
    _seed(db_session)
    row = PlayerStatLine(
        sport_code="AFL", season_year=2024, round_number=1, team_name="Carlton",
        player_name="Acres, Blake", player_source_id="players/B/Blake_Acres.html",
        recorded_at=datetime.now(timezone.utc), stats={"disposals": 25},
        jumper_number=13, subbed_on=False, subbed_off=True,
    )
    ingest_player_stats(db_session, [row], season_year=2024)

    stat = db_session.scalar(select(PlayerMatchStat))
    assert stat.jumper_number == 13
    assert stat.subbed_off is True
    assert stat.subbed_on is False

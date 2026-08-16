from datetime import datetime, timezone

from sqlalchemy import select

from app.ingestion.player_stats import ingest_player_stats
from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.providers.afl.round_labels import RoundKind, RoundLabel
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
        round_label=RoundLabel(raw=str(round_number), kind=RoundKind.HOME_AND_AWAY, round_number=round_number),
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


def _finals_row(team, kind, player_name="Acres, Blake", player_source_id="players/B/Blake_Acres.html", **stats) -> PlayerStatLine:
    raw_by_kind = {
        RoundKind.FINALS_WEEK_1: "EF",
        RoundKind.SEMI_FINALS: "SF",
        RoundKind.PRELIMINARY_FINAL: "PF",
        RoundKind.GRAND_FINAL: "GF",
    }
    return PlayerStatLine(
        sport_code="AFL",
        season_year=2024,
        round_label=RoundLabel(raw=raw_by_kind[kind], kind=kind, round_number=None),
        team_name=team,
        player_name=player_name,
        player_source_id=player_source_id,
        recorded_at=datetime.now(timezone.utc),
        stats=stats,
    )


def _add_round(db_session, season, round_number, name) -> Round:
    round_ = Round(season_id=season.id, round_number=round_number, name=name)
    db_session.add(round_)
    db_session.flush()
    return round_


def test_finals_row_resolves_via_round_name(db_session):
    seed = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    finals_round = _add_round(db_session, seed["season"], round_number=25, name="Finals Week 1")
    finals_match = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=finals_round.id,
        home_team_id=seed["home"].id, away_team_id=seed["away"].id,
        scheduled_start=datetime(2024, 9, 7, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=100, away_score=90,
    )
    db_session.add(finals_match)
    db_session.commit()

    result = ingest_player_stats(db_session, [_finals_row("Carlton", RoundKind.FINALS_WEEK_1, disposals=22)], season_year=2024)

    assert result.stats_created == 1
    assert result.unmatched == []
    stat = db_session.scalar(select(PlayerMatchStat))
    assert stat.match_id == finals_match.id
    assert stat.disposals == 22


def test_qf_and_ef_both_resolve_to_finals_week_1(db_session):
    """Squiggle doesn't distinguish EF from QF - both map to the same round
    name, and since a team plays at most one of them, resolution is still
    exact regardless of which code the source used."""
    seed = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    finals_round = _add_round(db_session, seed["season"], round_number=25, name="Finals Week 1")
    finals_match = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=finals_round.id,
        home_team_id=seed["home"].id, away_team_id=seed["away"].id,
        scheduled_start=datetime(2024, 9, 7, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=100, away_score=90,
    )
    db_session.add(finals_match)
    db_session.commit()

    qf_row = PlayerStatLine(
        sport_code="AFL", season_year=2024,
        round_label=RoundLabel(raw="QF", kind=RoundKind.FINALS_WEEK_1, round_number=None),
        team_name="Carlton", player_name="Acres, Blake", player_source_id="players/B/Blake_Acres.html",
        recorded_at=datetime.now(timezone.utc), stats={"disposals": 20},
    )
    result = ingest_player_stats(db_session, [qf_row], season_year=2024)

    assert result.stats_created == 1
    assert db_session.scalar(select(PlayerMatchStat)).match_id == finals_match.id


def test_finals_row_reported_unmatched_when_round_missing(db_session):
    """The team's season has no Grand Final round at all (they didn't make
    it that far) - must be reported, not guessed against some other round."""
    _seed(db_session, home="Carlton", away="Richmond", round_number=1)

    result = ingest_player_stats(db_session, [_finals_row("Carlton", RoundKind.GRAND_FINAL, disposals=20)], season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "Grand Final" in result.unmatched[0]


def test_finals_ambiguous_match_is_reported_not_guessed(db_session):
    """A team appearing twice in the same finals round shouldn't happen in
    real data, but must not be silently resolved to either one."""
    seed = _seed(db_session, home="Carlton", away="Richmond", round_number=1)
    finals_round = _add_round(db_session, seed["season"], round_number=25, name="Finals Week 1")
    geelong = Team(sport_id=seed["sport"].id, name="Geelong", short_name="GEE")
    db_session.add(geelong)
    db_session.flush()
    match1 = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=finals_round.id,
        home_team_id=seed["home"].id, away_team_id=seed["away"].id,
        scheduled_start=datetime(2024, 9, 7, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=100, away_score=90,
    )
    match2 = Match(
        sport_id=seed["sport"].id, season_id=seed["season"].id, round_id=finals_round.id,
        home_team_id=seed["home"].id, away_team_id=geelong.id,
        scheduled_start=datetime(2024, 9, 8, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=80, away_score=70,
    )
    db_session.add_all([match1, match2])
    db_session.commit()

    result = ingest_player_stats(db_session, [_finals_row("Carlton", RoundKind.FINALS_WEEK_1, disposals=20)], season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "ambiguous" in result.unmatched[0].lower()


def _seed_bye_scenario(db_session):
    """Carlton plays rounds 1 and 3 in Squiggle (round 2 is a bye), but the
    AFL Tables source reports Carlton's data under labels "1" and "2" (its
    own round-numbering disagrees with Squiggle's by one, for round 2
    onward) - the real pattern that motivated the fallback resolver."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round1 = Round(season_id=season.id, round_number=1)
    round2 = Round(season_id=season.id, round_number=2)  # exists in Squiggle (other teams play) - just not for Carlton
    round3 = Round(season_id=season.id, round_number=3)
    round5 = Round(season_id=season.id, round_number=5)  # same - used by the ambiguous-fallback test
    carlton = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    richmond = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round1, round2, round3, round5, carlton, richmond])
    db_session.flush()
    match1 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round1.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 3, 14, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    match3 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round3.id,
        home_team_id=richmond.id, away_team_id=carlton.id,
        scheduled_start=datetime(2024, 3, 28, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=70, away_score=95,
    )
    db_session.add_all([match1, match3])
    db_session.commit()
    return {"sport": sport, "season": season, "carlton": carlton, "richmond": richmond, "match1": match1, "match3": match3}


def test_fallback_resolves_unique_leftover_pairing(db_session):
    seed = _seed_bye_scenario(db_session)
    # round 1 matches directly; round 2 (source) has no Squiggle round 2 at
    # all, and round 3 (Squiggle) has no source round 3 - exactly one
    # unresolved source round, exactly one unclaimed Squiggle match.
    rows = [
        _row("Carlton", round_number=1, disposals=18),
        _row("Carlton", round_number=2, disposals=24),
    ]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 2
    assert result.fallback_resolved == 1
    assert result.unmatched == []

    stats_by_match = {s.match_id: s for s in db_session.scalars(select(PlayerMatchStat)).all()}
    assert stats_by_match[seed["match1"].id].disposals == 18
    assert stats_by_match[seed["match3"].id].disposals == 24  # resolved via fallback


def test_fallback_reports_ambiguous_when_leftover_counts_differ(db_session):
    """Two unresolved source rounds but only one unclaimed match - the
    brief's explicit "if multiple candidates remain, leave unmatched"
    case. Nothing should be guessed."""
    seed = _seed_bye_scenario(db_session)
    rows = [
        _row("Carlton", round_number=1, disposals=18),
        _row("Carlton", round_number=2, disposals=24),
        _row("Carlton", round_number=5, disposals=30),  # a second, genuinely-spurious leftover
    ]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 1  # only round 1 (direct match) resolves
    assert result.fallback_resolved == 0
    assert len(result.unmatched) == 2  # rounds 2 and 5 both left unresolved
    assert any("refusing to guess" in m for m in result.unmatched)


def test_fallback_pairs_multiple_leftovers_in_chronological_order(db_session):
    """Two unresolved source rounds, two unclaimed matches - resolved by
    pairing ascending source round number with ascending match date, the
    one order-preserving pairing possible."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round1 = Round(season_id=season.id, round_number=1)
    round2 = Round(season_id=season.id, round_number=2)  # exists (other teams play) - no Carlton match
    round3 = Round(season_id=season.id, round_number=3)  # same
    round4 = Round(season_id=season.id, round_number=4)
    round7 = Round(season_id=season.id, round_number=7)
    carlton = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    richmond = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round1, round2, round3, round4, round7, carlton, richmond])
    db_session.flush()
    match1 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round1.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 3, 14, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    match4 = Match(  # earlier of the two leftovers
        sport_id=sport.id, season_id=season.id, round_id=round4.id,
        home_team_id=richmond.id, away_team_id=carlton.id,
        scheduled_start=datetime(2024, 4, 4, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=70, away_score=95,
    )
    match7 = Match(  # later of the two leftovers
        sport_id=sport.id, season_id=season.id, round_id=round7.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 4, 25, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=60, away_score=65,
    )
    db_session.add_all([match1, match4, match7])
    db_session.commit()

    rows = [
        _row("Carlton", round_number=1, disposals=18),
        _row("Carlton", round_number=2, disposals=24),  # -> should pair with the earlier leftover (match4)
        _row("Carlton", round_number=3, disposals=29),  # -> should pair with the later leftover (match7)
    ]
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 3
    assert result.fallback_resolved == 2
    assert result.unmatched == []

    stats_by_match = {s.match_id: s for s in db_session.scalars(select(PlayerMatchStat)).all()}
    assert stats_by_match[match1.id].disposals == 18
    assert stats_by_match[match4.id].disposals == 24
    assert stats_by_match[match7.id].disposals == 29


def test_fallback_deterministic_across_reruns(db_session):
    seed = _seed_bye_scenario(db_session)
    rows = [
        _row("Carlton", round_number=1, disposals=18),
        _row("Carlton", round_number=2, disposals=24),
    ]

    ingest_player_stats(db_session, rows, season_year=2024)
    result = ingest_player_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert result.stats_updated == 0
    assert result.stats_unchanged == 2
    assert len(db_session.scalars(select(PlayerMatchStat)).all()) == 2  # no duplicates


def test_subs_and_jumper_number_persisted(db_session):
    _seed(db_session)
    row = PlayerStatLine(
        sport_code="AFL", season_year=2024,
        round_label=RoundLabel(raw="1", kind=RoundKind.HOME_AND_AWAY, round_number=1), team_name="Carlton",
        player_name="Acres, Blake", player_source_id="players/B/Blake_Acres.html",
        recorded_at=datetime.now(timezone.utc), stats={"disposals": 25},
        jumper_number=13, subbed_on=False, subbed_off=True,
    )
    ingest_player_stats(db_session, [row], season_year=2024)

    stat = db_session.scalar(select(PlayerMatchStat))
    assert stat.jumper_number == 13
    assert stat.subbed_off is True
    assert stat.subbed_on is False

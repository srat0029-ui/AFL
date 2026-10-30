-- NBA evidence audit (read-only, aggregates only): how much 2026-27 evidence
-- and how many frozen outlooks with known outcomes exist so far.
with season as (
    select min(scheduled_start) as starts_at
    from nba_games
    where season_start_year = 2026 and season_type = 'regular'
)
select 'availability observations since the season start' as item, count(*)::text as value
from nba_player_availability_reports r, season
where r.observed_at >= season.starts_at
union all
select 'players the injury feed listed since the season start', count(distinct r.player_id)::text
from nba_player_availability_reports r, season
where r.observed_at >= season.starts_at and r.is_listed
union all
select 'pregame lineup observations (competitive 2026-27 games)', count(*)::text
from nba_game_lineup_observations o
join nba_games g on g.id = o.game_id
where g.season_start_year = 2026 and g.season_type in ('regular', 'play_in', 'playoffs')
union all
select 'final competitive 2026-27 games with a full box score', count(*)::text
from nba_games g
where g.season_start_year = 2026 and g.season_type in ('regular', 'play_in', 'playoffs')
  and g.status = 'final' and g.box_score_state = 'ingested'
union all
select 'produced ' || s.snapshot_label || ' snapshots of those games', count(*)::text
from nba_outlook_snapshots s
join nba_games g on g.id = s.game_id
where s.status = 'produced' and g.status = 'final' and g.box_score_state = 'ingested'
group by s.snapshot_label;

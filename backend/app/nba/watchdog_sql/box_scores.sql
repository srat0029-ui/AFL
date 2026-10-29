-- NBA watchdog (read-only): competitive games that tipped off between 6 hours
-- and 3 days ago (inside the live cycle's box-score look-back) but are not
-- final with a stored box score. Every row is a problem. Postponed and
-- cancelled games are excluded. See app/nba/watchdog.py.
select
    g.id as game_id,
    to_char(g.scheduled_start at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') as tipoff,
    away.abbreviation || '@' || home.abbreviation as matchup,
    g.status,
    coalesce(g.box_score_state, '') as box_score_state
from nba_games g
join nba_teams home on home.id = g.home_team_id
join nba_teams away on away.id = g.away_team_id
where g.season_type in ('regular', 'play_in', 'playoffs')
  and g.status not in ('postponed', 'cancelled')
  and g.scheduled_start between now() - interval '3 days' and now() - interval '6 hours'
  and not (g.status = 'final' and coalesce(g.box_score_state, '') in ('ingested', 'ingested_partial'))
order by g.scheduled_start, g.id;

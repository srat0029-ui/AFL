-- NBA watchdog (read-only): T-4h and T-1h V1.5 snapshots that have player rows
-- but no frozen Stat Model V1 predictions, after their freeze window has closed.
-- Every row is a problem. See app/nba/watchdog.py and
-- app/nba/stats/prospective.py.
--
-- The freeze step uses continue-on-error, and a closed window exits 0, so a
-- green live cycle says nothing about whether predictions were written. These
-- gaps are permanent: predictions are never back-filled.
--
-- A window counts only if its target cutoff is on or after
-- watchdog.stat_since (when collection was switched on), and once target
-- cutoff + 30 minutes of tolerance + 20 minutes of grace has passed. As with
-- snapshots, a game ages out of the rolling watchdog.window_hours window.
with params as (
    select
        current_setting('watchdog.stat_since')::timestamptz as since,
        make_interval(hours => current_setting('watchdog.window_hours')::int) as window_size,
        now() as checked_at
)
select
    s.game_id,
    to_char(s.scheduled_tipoff at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') as tipoff,
    away.abbreviation || '@' || home.abbreviation as matchup,
    s.snapshot_label as label,
    s.id as snapshot_id
from nba_outlook_snapshots s
join nba_games g on g.id = s.game_id
join nba_teams home on home.id = g.home_team_id
join nba_teams away on away.id = g.away_team_id
cross join params p
where s.snapshot_label in ('T-4h', 'T-1h')
  and s.status in ('produced', 'stale', 'partial')
  and g.status not in ('postponed', 'cancelled')
  and s.target_cutoff >= p.since
  and s.target_cutoff + interval '50 minutes' < p.checked_at
  and s.scheduled_tipoff between p.checked_at - p.window_size and p.checked_at + p.window_size
  and not exists (
      select 1
      from nba_prop_projections f
      where f.game_id = s.game_id and f.information_cutoff = s.information_cutoff and f.model_name = 'stat_v1'
  )
order by s.scheduled_tipoff, s.game_id, s.snapshot_label;

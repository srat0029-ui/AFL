-- NBA watchdog (read-only): one row per (competitive game in the rolling
-- monitoring window, standard snapshot label), with whether that window is
-- DUE and the status frozen for it ('' when none). See app/nba/watchdog.py
-- and .github/workflows/nba-watchdog.yml.
--
-- Rolling monitoring window: games whose CURRENT scheduled tip-off is within
-- watchdog.window_hours of now, either side. This is deliberately not "every
-- snapshot since activation": a missed snapshot is permanent (frozen
-- snapshots are never redone), so a gap is reported while its game is in
-- the window and then ages out rather than alerting forever.
--
-- A snapshot only covers a window when it was taken for the game's CURRENT
-- tip-off (scheduled_tipoff = scheduled_start). A snapshot frozen for an
-- earlier tip-off of a rescheduled game describes a different cutoff, and
-- the runner takes the moved game's windows afresh.
--
-- A window is due once its target cutoff (tip-off - offset) is on or after
-- watchdog.since (when snapshots were switched on) and target cutoff +
-- tolerance (min(30 min, offset / 2), as in app/nba/outlooks/snapshots.py)
-- + 20 minutes of grace for the 15-minute cycle has passed.
--
-- Both settings are passed in PGOPTIONS, e.g.
-- "-c watchdog.since=2026-10-29T01:28:47Z -c watchdog.window_hours=24".
with params as (
    select
        current_setting('watchdog.since')::timestamptz as since,
        make_interval(hours => current_setting('watchdog.window_hours')::int) as window_size,
        now() as checked_at
),
labels(label, offset_min, tolerance_min) as (
    values ('T-24h', 1440, 30), ('T-4h', 240, 30), ('T-1h', 60, 30), ('T-30m', 30, 15)
),
games as (
    select g.id, g.scheduled_start, home.abbreviation as home_abbr, away.abbreviation as away_abbr
    from nba_games g
    join nba_teams home on home.id = g.home_team_id
    join nba_teams away on away.id = g.away_team_id
    cross join params p
    where g.season_type in ('regular', 'play_in', 'playoffs')
      and g.status not in ('postponed', 'cancelled')
      and g.scheduled_start between p.checked_at - p.window_size and p.checked_at + p.window_size
)
select
    g.id as game_id,
    to_char(g.scheduled_start at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') as tipoff,
    g.away_abbr || '@' || g.home_abbr as matchup,
    l.label,
    (g.scheduled_start - make_interval(mins => l.offset_min) >= p.since
     and g.scheduled_start - make_interval(mins => l.offset_min) + make_interval(mins => l.tolerance_min + 20) < p.checked_at) as due,
    coalesce((
        select s.status
        from nba_outlook_snapshots s
        where s.game_id = g.id and s.snapshot_label = l.label and s.scheduled_tipoff = g.scheduled_start
        order by (s.status = 'produced') desc, s.id desc
        limit 1
    ), '') as status
from games g
cross join labels l
cross join params p
order by g.scheduled_start, g.id, l.offset_min desc;

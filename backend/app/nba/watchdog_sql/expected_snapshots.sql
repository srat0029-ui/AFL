-- NBA watchdog (read-only): one row per (competitive game tipping off within
-- 24 hours either side of now, standard snapshot label), with whether that
-- window is DUE and the status frozen for it ('' when none). See
-- app/nba/watchdog.py and .github/workflows/nba-watchdog.yml.
--
-- A window is due once its target cutoff (tip-off - offset) is on or after
-- watchdog.since (when snapshots were switched on) and target cutoff +
-- tolerance (min(30 min, offset / 2), as in app/nba/outlooks/snapshots.py)
-- + 20 minutes of grace for the 15-minute cycle has passed.
-- watchdog.since is a custom setting passed in PGOPTIONS
-- ("-c watchdog.since=2026-10-29T01:28:47Z").
with params as (
    select current_setting('watchdog.since')::timestamptz as since, now() as checked_at
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
      and g.scheduled_start between p.checked_at - interval '24 hours' and p.checked_at + interval '24 hours'
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
        where s.game_id = g.id and s.snapshot_label = l.label
        order by (s.status = 'produced') desc, s.id desc
        limit 1
    ), '') as status
from games g
cross join labels l
cross join params p
order by g.scheduled_start, g.id, l.offset_min desc;

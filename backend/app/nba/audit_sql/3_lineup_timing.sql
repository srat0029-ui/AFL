-- NBA evidence audit (read-only, aggregates only): when, relative to tip-off,
-- ESPN's per-game roster carries starters and shrinks to the active list.
-- Observations are stored only when they change, so a bucket's rows are
-- changes first seen in that bucket. Decides whether "confirmed starter" or
-- "dressed" can be V2 inputs at the T-1h / T-30m snapshots.
with obs as (
    select
        o.*,
        extract(epoch from (o.tipoff_at_observation - o.observed_at)) / 60.0 as minutes_before_tip
    from nba_game_lineup_observations o
    join nba_games g on g.id = o.game_id
    where g.season_start_year = 2026 and g.season_type in ('regular', 'play_in', 'playoffs')
)
select
    case
        when minutes_before_tip < 0 then '6 after tip-off'
        when minutes_before_tip < 15 then '5 0-15 min before'
        when minutes_before_tip < 30 then '4 15-30 min before'
        when minutes_before_tip < 60 then '3 30-60 min before'
        when minutes_before_tip < 240 then '2 1-4 h before'
        else '1 4 h+ before'
    end as bucket,
    count(*) as observations,
    count(distinct (game_id, team_id)) as team_games,
    count(*) filter (where has_starter_field) as with_starter_field,
    count(*) filter (where starters_flagged > 0) as with_starters,
    count(*) filter (where lineup_available) as lineup_available,
    round(avg(players_listed), 1) as mean_players_listed,
    min(players_listed) as min_players_listed,
    max(players_listed) as max_players_listed
from obs
group by 1
order by 1;

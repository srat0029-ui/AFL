-- NBA evidence audit (read-only, aggregates only): which statuses the injury
-- feed has actually published since the regular season started. Decides
-- whether Minutes V2 can use Questionable / Doubtful / Probable or only
-- Out / Day-To-Day.
with season as (
    select min(scheduled_start) as starts_at
    from nba_games
    where season_start_year = 2026 and season_type = 'regular'
)
select
    r.is_listed,
    coalesce(r.source_status, '') as source_status,
    coalesce(r.status, '') as status,
    coalesce(r.fantasy_status, '') as fantasy_status,
    count(*) as observations,
    count(distinct r.player_id) as players,
    count(*) filter (where r.team_id is null) as no_team,
    count(*) filter (where r.source_team_id is distinct from r.source_athlete_team_id) as team_disagreement
from nba_player_availability_reports r, season
where r.observed_at >= season.starts_at
group by 1, 2, 3, 4
order by observations desc;

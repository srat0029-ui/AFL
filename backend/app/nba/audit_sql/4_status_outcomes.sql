-- NBA evidence audit (read-only, aggregates only): for frozen V1.5 outlooks
-- of games now final, how the injury-feed state recorded WITH the outlook
-- (at its cutoff) relates to what happened. "Dressed" = listed in the box
-- score; "played" = listed and not did-not-play. Mean P(play) is V1.5's own
-- evidence-blind estimate, for comparison. Only games with a full box score.
--
-- These games are DESIGN data for Minutes V2. V2 will be tested on games
-- after a date fixed in advance, never on the games summarised here.
with preds as (
    select
        s.snapshot_label,
        s.offset_minutes,
        r.game_id,
        r.player_id,
        r.p_play,
        coalesce(r.availability_evidence ->> 'state', '') as state,
        coalesce(nullif(r.availability_evidence ->> 'status', ''), lower(r.availability_evidence ->> 'source_status'), '') as status
    from nba_rotation_predictions r
    join nba_outlook_snapshots s on s.id = r.snapshot_id
    join nba_games g on g.id = r.game_id
    where s.status = 'produced' and g.status = 'final' and g.box_score_state = 'ingested'
)
select
    p.snapshot_label,
    p.state,
    p.status,
    count(*) as candidates,
    round(avg((l.id is not null)::int), 3) as dressed_rate,
    round(avg((l.id is not null and not l.did_not_play)::int), 3) as played_rate,
    round(avg(p.p_play)::numeric, 3) as mean_p_play,
    round(avg(case when l.id is not null and not l.did_not_play then l.minutes end)::numeric, 1) as mean_minutes_if_played
from preds p
left join nba_player_game_logs l on l.game_id = p.game_id and l.player_id = p.player_id
group by p.snapshot_label, p.state, p.status
order by min(p.offset_minutes) desc, candidates desc;

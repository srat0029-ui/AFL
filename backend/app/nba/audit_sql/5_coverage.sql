-- NBA evidence audit (read-only, aggregates only): players who played in a
-- final game but had no outlook in that game's frozen snapshot (not on the
-- observed roster, not yet in nba_players, or no history), and the share of
-- minutes they played. Bounds what any V2 built on these candidates can see.
select
    s.snapshot_label,
    count(distinct (l.game_id, l.team_id)) as team_games,
    count(*) filter (where not l.did_not_play) as played,
    count(*) filter (where not l.did_not_play and r.id is null) as played_without_outlook,
    count(*) filter (where not l.did_not_play and r.id is not null and r.expected_minutes_if_plays is null) as played_without_minutes,
    round(coalesce(sum(l.minutes) filter (where r.id is null), 0)::numeric / nullif(sum(l.minutes), 0)::numeric, 4) as minutes_share_without_outlook
from nba_outlook_snapshots s
join nba_games g on g.id = s.game_id
join nba_player_game_logs l on l.game_id = s.game_id
left join nba_rotation_predictions r on r.snapshot_id = s.id and r.player_id = l.player_id
where s.status = 'produced' and g.status = 'final' and g.box_score_state = 'ingested'
group by s.snapshot_label
order by min(s.offset_minutes) desc;

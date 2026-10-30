-- NBA evidence audit (read-only, aggregates only): is ESPN's per-game list,
-- as known at each snapshot's cutoff, the active list? For every produced
-- snapshot of a final game and each team, the latest lineup observation at
-- or before the snapshot's information cutoff is compared player by player
-- with the box-score listing (who dressed), and split across the snapshot's
-- roster candidates. If candidates OFF the list almost never dress, the
-- inactives are known at that cutoff and Minutes V2's "active list known"
-- gain is largely attainable there.
with snaps as (
    select s.id as snapshot_id, s.snapshot_label, s.offset_minutes, s.game_id, s.information_cutoff, g.scheduled_start, g.home_team_id, g.away_team_id
    from nba_outlook_snapshots s
    join nba_games g on g.id = s.game_id
    where s.status = 'produced' and g.status = 'final' and g.box_score_state = 'ingested'
),
team_snaps as (
    select sn.*, t.team_id
    from snaps sn
    cross join lateral (values (sn.home_team_id), (sn.away_team_id)) as t(team_id)
),
lists as (
    select ts.snapshot_id, ts.snapshot_label, ts.offset_minutes, ts.game_id, ts.team_id, o.id as obs_id,
           extract(epoch from (ts.scheduled_start - o.observed_at)) / 60.0 as list_minutes_before_tip
    from team_snaps ts
    left join lateral (
        select o.id, o.observed_at
        from nba_game_lineup_observations o
        where o.game_id = ts.game_id and o.team_id = ts.team_id and o.observed_at <= ts.information_cutoff
        order by o.observed_at desc, o.id desc
        limit 1
    ) o on true
),
list_entries as (
    select l.snapshot_id, l.team_id, e ->> 'id' as source_player_id, p.id as player_id
    from lists l
    join nba_game_lineup_observations o on o.id = l.obs_id
    cross join lateral json_array_elements(o.payload::json -> 'entries') as e
    left join nba_players p on p.source = 'espn' and p.source_player_id = e ->> 'id'
),
dressed as (
    select l.snapshot_id, l.team_id, lg.player_id
    from lists l
    join nba_player_game_logs lg on lg.game_id = l.game_id and lg.team_id = l.team_id
),
candidates as (
    select l.snapshot_id, l.team_id, r.player_id,
           exists (select 1 from list_entries le where le.snapshot_id = l.snapshot_id and le.team_id = l.team_id and le.player_id = r.player_id) as on_list,
           exists (select 1 from dressed d where d.snapshot_id = l.snapshot_id and d.team_id = l.team_id and d.player_id = r.player_id) as dressed
    from lists l
    join nba_rotation_predictions r on r.snapshot_id = l.snapshot_id and r.team_id = l.team_id
    where l.obs_id is not null
)
select
    l.snapshot_label,
    count(*) as team_games,
    count(l.obs_id) as with_list,
    round(avg(l.list_minutes_before_tip)::numeric, 0) as mean_list_age_min_before_tip,
    (select count(*) from list_entries le join lists x on x.snapshot_id = le.snapshot_id and x.team_id = le.team_id where x.snapshot_label = l.snapshot_label) as list_entries,
    (select count(*) from list_entries le join lists x on x.snapshot_id = le.snapshot_id and x.team_id = le.team_id where x.snapshot_label = l.snapshot_label and le.player_id is null) as list_entries_unresolved,
    (select count(*) from dressed d join lists x on x.snapshot_id = d.snapshot_id and x.team_id = d.team_id where x.snapshot_label = l.snapshot_label and x.obs_id is not null) as dressed,
    (select count(*) from dressed d join lists x on x.snapshot_id = d.snapshot_id and x.team_id = d.team_id
      where x.snapshot_label = l.snapshot_label and exists (select 1 from list_entries le where le.snapshot_id = d.snapshot_id and le.team_id = d.team_id and le.player_id = d.player_id)) as dressed_on_list,
    (select count(*) from list_entries le join lists x on x.snapshot_id = le.snapshot_id and x.team_id = le.team_id
      where x.snapshot_label = l.snapshot_label and not exists (select 1 from dressed d where d.snapshot_id = le.snapshot_id and d.team_id = le.team_id and d.player_id = le.player_id)) as on_list_not_dressed,
    (select count(*) from candidates c join lists x on x.snapshot_id = c.snapshot_id and x.team_id = c.team_id where x.snapshot_label = l.snapshot_label and c.on_list) as candidates_on_list,
    (select round(avg(c.dressed::int), 3) from candidates c join lists x on x.snapshot_id = c.snapshot_id and x.team_id = c.team_id where x.snapshot_label = l.snapshot_label and c.on_list) as dressed_rate_on_list,
    (select count(*) from candidates c join lists x on x.snapshot_id = c.snapshot_id and x.team_id = c.team_id where x.snapshot_label = l.snapshot_label and not c.on_list) as candidates_off_list,
    (select round(avg(c.dressed::int), 3) from candidates c join lists x on x.snapshot_id = c.snapshot_id and x.team_id = c.team_id where x.snapshot_label = l.snapshot_label and not c.on_list) as dressed_rate_off_list
from lists l
group by l.snapshot_label
order by min(l.offset_minutes) desc;

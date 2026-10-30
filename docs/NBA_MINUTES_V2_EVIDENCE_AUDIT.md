# Minutes V2, step 0: evidence audit — first run

Run [41011728358](https://github.com/srat0029-ui/AFL/actions/runs/41011728358),
2026-10-30 06:02Z, read-only, aggregates only
(`.github/workflows/nba-evidence-audit.yml`). Everything summarised here is
**design-period** data (plan: test period starts 2026-12-01).

## Volume

- 70 final competitive 2026-27 games with full box scores, but only **3** of
  them have produced T-1h / T-30m snapshots (2 at T-4h; none at T-24h yet).
  Snapshots started 29 Oct, so outcome-linked V2 data is just beginning.
- 319 injury-feed observations since the season start, 111 players listed;
  125 pregame lineup observations.

## 1. Feed vocabulary: Out and Day-To-Day only

| source_status | fantasy_status | observations | players |
|---|---|---:|---:|
| Out | OUT | 118 | 84 |
| Day-To-Day | GTD | 79 | 48 |
| Out | GTD | 30 | 16 |
| Out | OFS (out for season) | 3 | 3 |
| (stopped being listed) | | 89 | 87 |

ESPN does **not** publish the league's Questionable / Doubtful / Probable in
season. The usable states are Out, Day-To-Day, no longer listed and never
listed, with ESPN's fantasy flag as a weak extra (30 "Out" entries carry
GTD). P(dresses | evidence) therefore has few categories, which suits a
small, shrunk recalibration but limits how much a Day-To-Day player's
status can say.

(The 89 "team disagreement" rows are all no-longer-listed rows, which carry
no team ids; among listed rows there is 1 genuine disagreement.)

## 2. Starters appear 15–30 minutes before tip-off

| Changes first seen | Observations | With starters | Mean players listed |
|---|---:|---:|---:|
| 4 h+ before | 58 | 0 | 14.0 |
| 1–4 h before | 16 | 0 | 16.7 |
| 30–60 min before | 22 | 0 | 13.6 |
| 15–30 min before | 11 | 10 | 13.6 |
| 0–15 min before | 18 | 18 | 13.4 |

- Starters are never present 30+ minutes out, and appear in the last
  15–30 minutes (lineup polling is every 15 minutes in the last hour, so
  timing is known to about 15 minutes).
- **None of the current snapshot windows (T-24h, T-4h, T-1h, T-30m) can
  use confirmed starters**: the T-30m cutoff is just before they appear.
  Using them would need a later window (e.g. T-10m), which is a production
  change for a later decision.
- The per-game list holds about 13–14 players from an hour out, close to a
  typical active list (about 13). Whether it **is** the active list — i.e.
  whether the inactives are already known at T-1h — is not answered by
  these counts. It is the most valuable open question: if yes, step 1's
  "active list known" scenario is largely attainable at T-1h.

## 3. Feed state vs outcome (3 games — far too few to estimate anything)

| Snapshot | State | Candidates | Dressed | Played | Mean V1.5 P(play) |
|---|---|---:|---:|---:|---:|
| T-1h | never listed | 62 | 95% | 71% | 0.70 |
| T-1h | listed Out | 19 | **0%** | 0% | **0.27** |
| T-1h | no longer listed | 18 | 83% | 78% | 0.75 |
| T-1h | Day-To-Day | 2 | 50% | 50% | 0.92 |

Directionally, every player listed Out at any snapshot (7, 19, 20) did not
dress, while V1.5 gave them P(play) around 0.27–0.30 — the simple Out rule
is the obvious first comparator. No rate here is precise enough to use.

## 4. Coverage

Every player who played in those games had an outlook (0 without; 0% of
minutes).

## 5. Per-game list vs who dressed (run 41013899976, same 3 games)

The latest per-game list known at each snapshot's cutoff, compared player
by player with the box score (6 team-games at T-1h / T-30m, 4 at T-4h; no
unresolved ids):

| Snapshot | Listed | Dressed | Dressed and listed | Listed, did not dress | Candidates on list: dressed | Candidates off list: dressed |
|---|---:|---:|---:|---:|---:|---:|
| T-4h | 53 | 51 | 45 (88%) | 8 | 0.85 of 53 | 0.42 of 12 |
| T-1h | 79 | 76 | 70 (92%) | 9 | 0.90 of 78 | 0.22 of 23 |
| T-30m | 82 | 76 | 74 (97%) | 8 | 0.91 of 80 | 0.10 of 21 |

- The list is **informative but not the active list**: about 10% of listed
  players do not dress, and some players off it do, less so nearer tip-off.
  Step 1's oracle is therefore not reachable at T-1h from the list alone.
- "Mean list age" is large (≈ 10 h at T-1h) because observations are stored
  only when the list changes; it says when the list last changed, not how
  stale our reading was.
- Not yet known: how much of the list overlaps with the injury feed's Out
  entries (20 Out vs 21 off-list candidates at T-30m suggests heavily). If
  the list adds little beyond Out, it is not worth a separate input.
- 6 team-games: indicative only.

## Implications

1. Participation (step 2) remains the priority: Out is already decisive;
   Day-To-Day and "no longer listed" need data, which accrues through
   November.
2. Next check: does the per-game list at T-1h / T-30m match who dressed?
   That needs a player-level comparison of the lineup observation's
   listed players with the box score — one more aggregate audit query.
3. Confirmed starters need a post-T-30m window; evaluate their offline
   value (game Y's starter flag as an oracle feature) before proposing it.

# NBA data sources

Where NBA data comes from, what was actually tested, how it is stored, and
what the stored history looks like. Every "tested" claim is the result of a
real request made from the development machine (Australia) on 2026-10-02,
not a reading of documentation — unless it says "documentation only".

## Summary

| Data | Source | State |
| --- | --- | --- |
| Teams, schedule, results | ESPN public site API | **Built; 2015-16 to 2026-27 loaded locally** |
| Player box scores | ESPN public site API | **Built; 2015-16 to 2025-26 loaded locally** |
| Injuries / availability, rosters, depth charts, pregame lineups | ESPN public APIs | **Collected prospectively** from 2 October 2026 — see [NBA_LIVE_EVIDENCE.md](NBA_LIVE_EVIDENCE.md). No history before that. |
| Bookmaker prop odds | The Odds API (existing client) | Events endpoint tested; **no quota spent, not built** |

ESPN is the V1 provider for schedule and historical box scores. No paid
statistics provider is in use.

## Stats, schedule and results

### What was tested

| Source | Result |
| --- | --- |
| `cdn.nba.com` (schedule, box score, scoreboard JSON) | **HTTP 403 "Access Denied"** on every file tried, five URLs across three seasons. |
| `stats.nba.com` (`leaguegamelog`) | **No response** — timed out at 25 seconds. |
| `site.api.espn.com` (scoreboard, summary, teams, injuries) | **HTTP 200** on everything tried. |
| balldontlie | Documentation only (needs a key). Free tier 5 requests/minute; $9.99/month for 60/minute; $39.99/month for 600/minute. The pricing page does not say which tier includes player box scores. |

The NBA-owned sources refused or ignored the requests. Those blocks were not
worked around — no header spoofing, no proxy.

### ESPN's limits

- **It is undocumented and unofficial.** ESPN publishes no contract, rate
  limit or terms for this API. It can change or disappear without notice,
  and automated use may not be something ESPN's terms of use permit.
- **Minutes are whole numbers** — see "Minutes" below.
- **Some older games have no usable box score** — see "Placeholder box
  scores" below.
- **No date ranges.** `dates=A-B` returned HTTP 400, so there is one request
  per date and one per game: about 1,600 requests for a season.
- **All-Star games are listed as regular-season events.** They are excluded
  by requiring both teams to be one of the 30 franchises.
- **Two per-player fields are unreliable and ignored:** `active` (false for
  bench players who played) and `reason` when the player did play (always
  "COACH'S DECISION").
- **Season labels differ.** ESPN names a season by the year it ends; this
  project by the year it starts.
- **Team names are current names.** Historical games are listed under each
  franchise's present-day name and id.

## How it is stored

### Identity and provenance

Nothing is matched on a display name alone.

| Record | Our canonical id | Provider identity kept | Provenance kept |
| --- | --- | --- | --- |
| Team | `nba_teams.id` | `external_ids["espn"]` (ESPN team id) | — |
| Game | `nba_games.id` | `source` + `source_game_id` (ESPN event id), unique | `source_status`, `source_season_type` (raw), `source_synced_at`, `box_score_state`, `box_score_synced_at` |
| Player | `nba_players.id` | `source` + `source_player_id` (ESPN athlete id), unique | `source_metadata["name_variants"]`, `last_game_at` |
| Player game log | `nba_player_game_logs.id` | via player and game; `source` | `recorded_at` (when the box score was fetched) |

- **Players changing teams.** A game log's `team_id` is the team the player
  appeared for in that game, taken from the box score. A traded player has
  one player row and correct per-game teams. `nba_players.current_team_id`
  is a display convenience that follows the player's most recent game only,
  so re-ingesting an old game cannot move them back.
- **Identical or similar names.** Two players with the same name are two
  rows, distinguished by provider id. The validation report lists every
  name shared by more than one player.
- **Name changes.** One player whose published name changes stays one row;
  the earlier spelling is kept for audit, not for matching.
- **Renamed or rebranded teams.** A team is identified by provider id; a
  rename updates the name on the same row.
- **A second provider.** Another source's team is attached to an existing
  team only by exact name, once; after that its own id identifies it. A
  differently spelled name is never fuzzily merged.
- **Inactive / did-not-play players** are stored as rows with
  `did_not_play = true`, the reason ESPN gave, and no statistics.

### Raw versus normalised values

Where a value is normalised, what the source actually said is stored beside
it: game status (`status` / `source_status`) and season type (`season_type` /
`source_season_type`). Where there is no safe canonical interpretation, the
raw value is stored and the canonical one is left empty — see injuries.

### Minutes

**ESPN supplies whole minutes** ("33"), not minutes and seconds.

- `nba_player_game_logs.minutes` stores exactly what ESPN gives, as a
  number. No seconds are invented.
- It is typed as a float so the model layer can treat minutes as a numeric
  quantity, and so a future source with finer precision needs no schema
  change. Nothing may assume second-level precision in current data.
- Consequence for modelling: each game's minutes carry up to about half a
  minute of rounding error, and a player ESPN shows at 0 minutes may have
  played a few seconds.

### Placeholder box scores

For some older games ESPN's box score is an empty shell: every player is
listed, but with "--" minutes and 0 for every statistic. First seen on
event `400827889` (Cleveland at Chicago, 27 October 2015), where every
player on both teams shows 0 points in a 97–95 game. ESPN's other endpoints
had no statistics for that game either.

Stored as published, that would record a star as scoring zero —
indistinguishable from a real result and poisonous to a model. So a box
score is stored only if, for each team, **the player points add up exactly
to the team's score**. One that fails is rejected whole (`box_score_state =
rejected`) and the game is counted as having no box score. How many games
this affects is in the validation results below.

### What counts as the modelling dataset

- **Competitive games** — regular season, play-in, playoffs — are the
  modelling dataset.
- **Preseason games** between two franchises are kept in the schedule,
  classified as preseason, but their box scores are not fetched and the
  as-of read path leaves them out by default.
- **All-Star and other exhibition events** are never stored.

Storing every season does not mean a model should train on every season
equally. Which training window and recency weighting to use is a modelling
question to be tested prospectively; nothing here decides it.

## Running it

```
python -m app.nba.cli backfill --from-season 2015 --to-season 2026
python -m app.nba.cli validate
```

**Idempotent:** every write is an upsert on a provider id.

**Resumable:** each date and each game is committed on its own. The
schedule sync records every date it has fetched and skips a past date whose
games were all settled; the box-score sync records each game's outcome and
fetches only games not yet successfully handled. An interrupted backfill is
continued by running the same command again.

**Pacing:** one request at a time with a pause between requests; no retries
and no parallel requests.

## Validation results

From `python -m app.nba.cli validate` on the local development database
after the full backfill, 2 October 2026. Production was not touched.

**Totals:** 16,284 games; 352,629 player-game rows (290,581 played, 62,048
did-not-play); 1,584 players; 30 teams. Game dates 2 October 2015 to
11 April 2027.

| Season | Games stored | Preseason | Regular final | Expected | Postseason final | Player rows | Players | Final games with no box score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2015-16 | 1,418 | 100 | 1,230 | 1,230 | 86 | 29,531 | 460 | 162 |
| 2016-17 | 1,409 | 97 | 1,230 | 1,230 | 79 | 29,303 | 474 | 168 |
| 2017-18 | 1,383 | 70 | 1,230 | 1,230 | 82 | 28,829 | 518 | 172 |
| 2018-19 | 1,378 | 66 | 1,230 | 1,230 | 82 | 32,361 | 545 | 23 |
| 2019-20 | 1,220 | 65 | 1,059 | 1,059 | 84 | 28,663 | 538 | 2 |
| 2020-21 | 1,252 | 49 | 1,080 | 1,080 | 91 | 31,372 | 544 | 7 |
| 2021-22 | 1,400 | 66 | 1,230 | 1,230 | 93 | 33,857 | 623 | 0 |
| 2022-23 | 1,386 | 65 | 1,230 | 1,230 | 90 | 34,033 | 549 | 0 |
| 2023-24 | 1,385 | 64 | 1,231 | 1,231 | 88 | 35,004 | 589 | 0 |
| 2024-25 | 1,396 | 70 | 1,231 | 1,231 | 90 | 35,198 | 580 | 0 |
| 2025-26 | 1,391 | 65 | 1,231 | 1,231 | 91 | 34,478 | 591 | 12 |
| 2026-27 | 1,266 | 66 | 0 | — | 0 | 0 | 0 | — (all 1,266 still scheduled) |

"Games stored" also counts postponed rows. Postseason is play-in plus
playoffs. Box scores were fetched for competitive games only, so preseason
games have no player rows by design.

**Season structure checks out.** Every completed season has exactly the
expected number of final regular-season games, including the two abnormal
ones: 2019-20 (1,059; suspended, finished in the Orlando bubble) and
2020-21 (1,080; 72 games a team, December to July). From 2023-24 the count
is 1,231 because ESPN lists the NBA Cup final as a regular-season game — an
83rd game for the two finalists (LAL–IND 2023-12-09, OKC–MIL 2024-12-17,
NY–SA 2025-12-16).

**Clean on:** duplicate games, duplicate players, duplicate player-game
rows, repeated matchups on a date, player rows whose team did not play in
the game, orphaned rows, thin box scores. In every stored box score the
player points add up to the team score (that is the condition for storing
it).

### Anomalies

1. **546 final games have no usable box score** (3.9% of 14,168
   competitive games). All were rejected by the points check; none failed
   to download.
   - **New Orleans, 2015-16 to 2017-18: every game** (255, including their
     nine 2018 playoff games). New Orleans has no player rows at all for
     those three seasons.
   - **Chicago, 2015-16 to 2017-18: every game but two** (250 of 252,
     including five of their six 2017 playoff games). Chicago has no player
     rows for 2017-18 and effectively none for the two seasons before.
   - Because those are two-team games, every other team is also missing
     its games against New Orleans and Chicago in those seasons — four to
     seven games per team in 2015-16, for example.
   - **2018-19: 23 games**, mostly Phoenix (14) and Philadelphia (8), where
     the player points exceed the score. ESPN lists some players twice
     under two ids in that period.
   - **All six 2021 play-in games**, plus one other 2020-21 game and two in
     2019-20.
   - **2025-26: 12 Chicago games** where one player row has no id and no
     statistics, so Chicago's points come up 2 to 7 short.
   - A sample of 30 of the 2015-16 rejections were all genuine empty shells
     (every player on both teams at 0 points), not near-misses.
2. **333 player rows are not marked did-not-play but have no minutes
   value.** None of them scored. Minutes is left empty; whether the player
   took the court is not known from this source.
3. **1,146 played rows show 0 minutes**, 22 of them with points. This is
   whole-minute rounding of very short stints.
4. **Five games have six starters listed for one team** (four of them
   Phoenix in late 2018). Stored as published.
5. **Ten names are held by two ESPN player ids each.** At least eight look
   like one person split across two ids by ESPN: a long-standing id plus a
   second id carrying one to five games, mostly February 2019 (Greg Monroe,
   Henry Ellenson, Isaiah Canaan, John Jenkins, Mitchell Creek, Ray
   Spalding, Tahjere McCall, Daryl Macon). Wayne Selden's 30 games for New
   York in 2021 sit under a second id. They are **not merged** — nothing
   here merges on a name — so 47 player-games are attached to a
   secondary id. Fixing it needs a reviewed id-alias table.
6. **43 player rows had no player id** and were left out. In the stored
   games those rows carried no points.
7. **73 postponed rows and one cancelled preseason game.** ESPN keeps a
   postponed game under its original id and lists the replay under a new
   one, so postponed rows are inert leftovers: they never get a box score
   and are not in the modelling dataset. 52 past dates hold such a row and
   are re-requested on each sync.
8. **No player was seen under a changed name**, so that path is covered by
   tests only.
9. **787 player-seasons span more than one team** — trades and signings,
   handled by storing the team per game. Not a defect.

### What this means for modelling

Decided defaults (also recorded in `app/nba/__init__.py`):

- **All seasons stay stored; player models train from 2018-19 by default.**
  Other training windows and recency weighting are to be compared
  prospectively.
- **Schedule features come from `nba_games`** — previous game, rest days,
  back-to-backs — never from the presence of player rows.
- **A game with no box score is an unavailable outcome**, never a zero-stat
  game.
- The incomplete 2015-16 to 2017-18 box scores are not being filled in.

- **2018-19 onward is close to complete** (44 gaps in 10,231
  competitive games).
- **2015-16 to 2017-18 are structurally incomplete**: two teams are absent
  and every other team is missing its games against them. A player's game
  count, rest days, back-to-backs and "previous game" computed from stored
  rows will be wrong around those gaps in those seasons. Rest and schedule
  features should be derived from `nba_games` (which is complete), not from
  the presence of player rows.
- **Minutes are whole numbers**, and are unknown for 333 rows.
- **No historical injury or lineup information exists**, so "who was
  expected to play" cannot be reconstructed for past games.

Database size after the backfill: 575,459,328 bytes (549 MB), up from
494,428,160 (472 MB) — the NBA history adds about 77 MB.

## Injuries and availability

Live collection is built and described in
[NBA_LIVE_EVIDENCE.md](NBA_LIVE_EVIDENCE.md). **Historical** injury
ingestion is not built, by decision, for the reasons below.

- ESPN's `/injuries` endpoint works: 59 entries across 24 teams on
  2 October 2026, each with a status, a date, comments and a player id.
- **Statuses are not guessed.** In preseason only `Out` and `Day-To-Day`
  appeared. `Day-To-Day` is not the league's questionable / doubtful /
  probable scale and is not mapped onto it. The schema now stores the raw
  source status in `source_status` (always) and leaves the canonical
  `status` empty when there is no safe mapping.
- **No historical injury ingestion.** The summary endpoint shows a game's
  injury list as it stands now, not as it stood before tip-off, so past
  pre-game availability cannot be reconstructed from this source. Loading
  it would put information from after the game into "what was known
  before" — exactly the leak the as-of boundary exists to prevent.
- Live availability tracking — recording what was known when — now exists;
  it starts from 2 October 2026 and cannot reach back before that.

## Bookmaker odds

No quota has been spent.

- **Tested (free endpoint):** listing NBA events with the configured key
  returned 44 upcoming games; opening games and tip-off times agree with
  ESPN's schedule.
- **Team names:** 29 of 30 match ESPN's exactly. The exception is the
  Clippers ("Los Angeles Clippers" at The Odds API, "LA Clippers" at ESPN).
  The Odds API identifies teams and players by name only, so mapping them
  to canonical ids must be an explicit, reviewed alias table plus exact
  matching — not fuzzy matching.
- **Documentation only:** NBA prop market keys (`player_points`,
  `player_rebounds`, `player_assists` and their `_alternate` variants), and
  that prop coverage is "mainly limited to US sports and US bookmakers".

### Intended odds strategy

- **US bookmakers** are expected to be the richer market and will serve
  first as the **reference / consensus** market.
- **Australian bookmakers** will later be tested as the **executable**
  market — the prices that can actually be bet.
- Both may be ingested. Australian coverage of NBA points, rebounds and
  assists props has not been tested and will be once quota is available.

What the code assumes today, so this stays open:

- The odds client takes a region per call; NBA's default is `us` and can be
  overridden. Nothing else names a region.
- Each bookmaker row has its own `region`, and each quote references its
  bookmaker, so quotes from several regions can sit side by side.
- The freeze step currently draws best price and consensus from the same
  set of eligible bookmakers. Separating "consensus from the reference
  region, entry price from the executable region" — and freezing which was
  which on the prediction — is a required change when odds ingestion is
  built. No model code exists that could depend on a region.

## Decisions still open

1. **Odds quota and regions** — needed before any prop quote is fetched.
2. **How to detect a withdrawn quote** (see MULTI_SPORT_ARCHITECTURE.md).
3. **Whether to fill the games ESPN has no box score for** from another
   source, or leave them as known gaps.
4. **Scheduling the live evidence cycle** so it runs without someone
   starting it.

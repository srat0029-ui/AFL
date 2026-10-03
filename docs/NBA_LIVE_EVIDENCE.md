# NBA live evidence

How the system records what was knowable before NBA games — player
availability above all — so that "what did we know about this player at
3:17 PM before this game?" can be answered exactly, later, from stored rows.

This information can only be collected going forward. ESPN shows the
current state of an injury list, a roster or a lineup, never what it showed
last week. Whatever is not recorded at the time is gone.

Nothing here predicts anything. No model, no recommendation, no odds, and no
API quota is involved.

## The rule

**Never overwrite history when new information arrives.**

- Every observation is stamped with `observed_at`: when this system
  received the response.
- A change in what the source shows is a **new row**. The earlier row is
  never edited or deleted — the ORM refuses both (see
  `app/core/prospective.py`).
- An unchanged state is not stored again. That the source was read again
  and still showed the same thing is recorded as a poll row.
- Every as-of query returns the latest observation with `observed_at` at or
  before the cutoff. The source's own date on an entry is never used for
  this.

A state's full temporal meaning is therefore: **first seen** at its row's
`observed_at`, **last confirmed** at the latest poll before the next row,
**superseded** when the next row appears.

## What ESPN exposes before a game

Everything below was tested with real requests on 2 October 2026, about 33
hours before the first 2026-27 preseason game (Miami at Toronto, tip-off
3 October 23:00 UTC). No game was closer to tip-off than that, so what
appears in the final hours before a game **has not been observed yet**.

| What | Endpoint | Before the game |
| --- | --- | --- |
| Injury / availability list | `site /injuries` | **Yes.** 59 entries across 24 teams. |
| Listed roster per team | `site /teams/{id}/roster` | **Yes.** 18–21 players per team, each with a roster status. |
| Roster for a specific game | `core /events/{id}/…/competitors/{team}/roster` | **Player ids only.** 19–22 players per team. |
| Starters | same endpoint | **No.** The `starter` field does not exist in the response before the game. |
| Inactive / did-not-play | same endpoint | **No.** The `active`, `didNotPlay` and `reason` fields do not exist before the game. |
| "Lineup available" flag | `core /events/{id}/competitions/{id}` | Present, and **false**. |
| Depth chart | `core /seasons/{y}/teams/{id}/depthcharts` | **Yes**, five positions with players in rank order. It is ESPN's editorial ordering, not an announced lineup. |
| Game status and tip-off time | `site /scoreboard` | **Yes.** |
| Postponements / reschedules | `site /scoreboard` | Status and time are visible; a change is recorded when seen. |
| Per-player box score rows | `site /summary` | **No.** The pregame summary has no player rows. |

For a **finished** game the per-game roster endpoint does carry `starter`,
`active`, `didNotPlay` and `reason` for every player.

### Can starters be known before tip-off?

**Not established.** At 33 hours out they are not available in any form.
After the game they are. Whether ESPN fills them in during the last hour
before tip-off is unknown, and it is not assumed either way.

The lineup observations exist to settle this with evidence: each one
records whether the starter field was present, how many starters were
flagged, the source's own lineup flag, and the tip-off time as known at
that moment. Once the cycle has run through real game days, the earliest
observation with starters present, measured against tip-off, is the answer.

There is **no historical pregame starter data**. The `started` flag on
stored box scores is known only after the game and must not be used as if
it had been known beforehand.

### Injury feed, in detail

- **Statuses seen:** `Out` (14) and `Day-To-Day` (45). Nothing else has
  been observed. The league's in-season terms (questionable, doubtful,
  probable) have not appeared yet.
- **Fields per entry:** an entry id, `status`, a status type
  (`INJURY_STATUS_OUT`, `INJURY_STATUS_DAYTODAY`), `date`, injury type,
  location, side, detail, an expected return date, a fantasy status
  (`GTD`, `OUT`, `OFS`), and a short and long comment.
- **Timestamps:** ESPN supplies two. Each entry has its own `date` — when
  ESPN says the status was set or updated — stored as
  `source_published_at`. The feed has a `timestamp`, which is simply when
  the response was generated. Neither replaces our own `observed_at`. Of
  the 59 entries, 8 were dated within the previous day and 20 more than a
  month earlier.
- **Only teams with entries appear.** Six teams were absent from the feed.
  Absence is not evidence of health.
- **No player id field.** The athlete object in this feed has no id; it
  appears only inside the athlete's link URL. It is taken from there (it
  is ESPN's own id). An entry with no such link is counted as unresolvable
  and is not matched by name.
- **Team disagreement.** For 8 of 59 entries the team the entry is listed
  under differs from the team on the athlete record. Both raw ids are
  stored; which is right is not known.
- **No game relationship.** The feed does not tie an entry to a game, so
  `game_id` is left empty. The game summary's own injury list was not used:
  it omitted a player the league feed and the team roster both listed.

## What is stored

| Table | One row is | Written when |
| --- | --- | --- |
| `nba_player_availability_reports` | a player's injury-feed state | the entry is new, changes in any way, or the player stops being listed |
| `nba_team_observations` | a team's listed roster, or its depth chart | it differs from the last one seen |
| `nba_game_lineup_observations` | one team's listed players for one game, with starter flags if present | it differs from the last one seen |
| `nba_game_schedule_observations` | a game's status, tip-off time and date | the game is first seen, or any of the three changes |
| `nba_evidence_polls` | one successful read of one source | every successful read |
| `nba_live_cycle_runs` | one run of the cycle and the outcome of each step | every run (bookkeeping; updated in place) |

All but the last are append-only and protected against edits and deletes.

### Availability rows

- `source_status` is exactly what ESPN published. `status` is the canonical
  value and is filled **only** when the raw status is one of the league's
  own terms (out, questionable, doubtful, probable, available). `Out` maps;
  `Day-To-Day` does not and is stored raw with `status` empty.
- **Disappearing from the feed** writes a row with `is_listed = false` and
  no status. It records that ESPN stopped listing the player at that time.
  It does not say he is healthy.
- A player the feed has never listed has no rows: no evidence, not
  "available".
- `raw` keeps ESPN's entry (without image and link clutter), so a later
  change to the normalisation can be re-derived from what was reported.
- A player not yet in `nba_players` — a rookie, say — is created from the
  feed by ESPN id.

### Guard against a broken feed

An append-only store cannot be cleaned up afterwards. If the feed lists
fewer than half the players it listed at the previous poll (once at least
20 are listed), the response is treated as broken and nothing is written;
the step is reported as failed and tried again next run.

## As-of queries

All in `app/nba/asof.py`, each taking a cutoff:

- `availability_known_at(db, player_id, cutoff)` — the player's latest
  observation at or before the cutoff, when that state was first seen, and
  when the feed was last read. No observation means no evidence.
- `team_availability_known_at(db, team_id, cutoff)` — every player the
  feed listed under a team at the cutoff; the basis for teammate features.
- `team_observation_known_at(db, team_id, kind, cutoff)` — roster or depth
  chart.
- `game_lineup_known_at(db, game_id, team_id, cutoff)`.
- `game_schedule_known_at(db, game_id, cutoff)` — the status and tip-off
  time believed at the cutoff, which can differ from `nba_games` now.

Over HTTP: `GET /api/nba/players/{id}/availability?as_of=2026-10-20T15:17:00Z`.

## The live cycle

```
python -m app.nba.cli run-live-cycle
python -m app.nba.cli run-live-cycle --force
```

One run does, in order: refresh the schedule window; read the injury feed;
read each team's roster and depth chart; read the lineup for each team in
games near tip-off; ingest box scores for games that have become final.

- **Safe to repeat.** A step that is not yet due is skipped without a
  single request. Identical observations are never stored twice.
- **Independent steps.** One failing is recorded and the others still run.
- **Recovers from interruption.** Every write is its own transaction. The
  run row is committed before the first step, so a killed run is visible;
  the next run marks it interrupted and does whatever is still due.
- **Postponed and rescheduled games.** `nba_games` is updated to the
  current state and the previous state is kept as a schedule observation.
  ESPN gives a replayed game a new id; the postponed row stays as it was.

### Polling frequency

Set in one place — `Settings` in `app/config.py`, each overridable by an
environment variable of the same name in capitals. Every interval is a
**minimum**: the cycle can be woken as often as you like and a source that
is not yet due is skipped without a request.

| Setting | Default | Meaning |
| --- | --- | --- |
| `NBA_POLL_AVAILABILITY_MINUTES` | 30 | injury feed |
| `NBA_POLL_SCHEDULE_MINUTES` | 180 | schedule window |
| `NBA_POLL_TEAM_ROSTERS_MINUTES` | 1440 | team rosters |
| `NBA_POLL_DEPTH_CHARTS_MINUTES` | 1440 | depth charts |
| `NBA_POLL_BOX_SCORES_MINUTES` | 60 | box scores for games that finished |
| `NBA_LINEUP_POLL_TIERS` | `24:60,4:30,1:15` | pregame lineups, by hours before tip-off (below) |
| `NBA_SCHEDULE_LOOKBACK_DAYS` / `LOOKAHEAD_DAYS` | 3 / 14 | schedule window around today |
| `NBA_LIVE_CYCLE_STALE_AFTER_MINUTES` | 40 | an unfinished run older than this is treated as dead |
| `NBA_MONITOR_STALE_MULTIPLIER` | 3 | evidence is "stale" after this many missed intervals |
| `NBA_REQUEST_INTERVAL_SECONDS` | 0.5 | pause between requests |

**Pregame lineups tighten as tip-off approaches**, to find out when ESPN
starts publishing starters:

| Time to tip-off | Poll every |
| --- | --- |
| more than 24 hours | not polled |
| 4 to 24 hours | 60 minutes |
| 1 to 4 hours | 30 minutes |
| under 1 hour | 15 minutes |
| after tip-off | not polled |

Each tier is `<hours>:<minutes>`; a game uses the smallest tier its
time-to-tip falls within. Only games still scheduled are polled, so a
postponed game drops out automatically.

At these defaults a busy game day is roughly 1,350 requests to ESPN, most
of them lineups (about 90 per game: 30 polls of one game-level and two
team-level requests).

### Scheduling

The hosted schedule is a GitHub Actions workflow
(`.github/workflows/nba-live-cycle.yml`) that wakes the cycle every 15
minutes against the persistent hosted database. It is committed but **not
active**; see [NBA_LIVE_SCHEDULER.md](NBA_LIVE_SCHEDULER.md) for its
design, the database it would use, and the steps to switch it on.

Until it is active, evidence exists only for the moments the cycle was run
by hand, and the gaps cannot be filled afterwards.

## Collected so far

One real run on 2 October 2026, into the local development database:

- 59 availability observations: 59 players, 24 teams; `Day-To-Day` 45,
  `Out` 14. Twelve of the players were new to the database.
- 30 roster observations (603 players, all with roster status "Active")
  and 30 depth charts.
- 6 lineup observations for the three nearest games, 33 and 57 hours
  before tip-off: players listed, no starter information.
- 66 schedule observations for upcoming preseason games.

## Limits

- Evidence starts on 2 October 2026. There is no availability, roster,
  depth-chart or pregame lineup history before that, and none can be
  recovered.
- Whether starters are published before tip-off is unknown.
- The injury feed's vocabulary has only been seen in preseason.
- The feed does not say which game an entry applies to.
- A player absent from the feed is unknown, not healthy.
- The write-once guard covers ORM writes; a bulk SQL statement bypasses it.
- ESPN's endpoints are unofficial and may change without notice.
- The free-text comments are ESPN's own writing. They are stored locally as
  evidence and are not reproduced in the repository's test fixtures.

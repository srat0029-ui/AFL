"""Reusable normalization for AFL Tables' round-column labels ("R5", "WF",
"EF", "QF", "SF", "PF", "GF") into a structured type, so parsing/matching
code never scatters its own string checks — see
app/providers/afl/afltables_players.py (parsing) and
app/ingestion/player_stats.py (match resolution), both of which consume
RoundLabel rather than raw strings.

Verified against real fetched pages (a team with a deep 2024 finals run):
AFL Tables' finals labels are EF (Elimination Final), QF (Qualifying
Final), SF (Semi Final), PF (Preliminary Final), GF (Grand Final) — bare
2-letter codes, no "R" prefix, in the same header row as the numbered
home-and-away rounds. From 2026 onward AFL Tables also publishes WF
(Wildcard Final) — the AFL introduced a distinct Wildcard Finals week that
season, played BEFORE the traditional Elimination/Qualifying week (real
2026 AFL Tables pages show it as the round immediately after the last
home-and-away round, e.g. "...R24, R25, WF, EF..."). Verified live against
both AFL Tables (afltables.com/afl/stats/teams/{team}/2026_gbg.html —
Carlton and Collingwood both show a WF column) and Squiggle
(api.squiggle.com.au/?q=games;year=2026 — round 25 has roundname="Wildcard
Finals", is_final=7, distinct from round 26's roundname="Finals Week 1",
is_final=2/3) during the investigation that added this support — a
genuine, real format change, not a guess.

Squiggle (this project's fixture source) does not distinguish EF from QF —
both are grouped under one Round named "Finals Week 1" per season, with
exactly 4 matches (verified against real ingested Round data across every
season 2016-2025: identical structure every year — "Finals Week 1" (4
matches), "Semi-Finals" (2), "Preliminary Finals" (2), "Grand Final" (1)).
Mapping EF and QF to the same RoundKind is therefore not a loss of
information for match resolution: a team appears in that round at most
once regardless of which of the two they played, so (season, team,
FINALS_WEEK_1) still resolves to exactly one match — see
app/ingestion/player_stats.py's finals resolution path. Wildcard Finals
needs no such grouping — AFL Tables has exactly one code (WF) for it, and
Squiggle already gives it its own distinct, real roundname ("Wildcard
Finals") rather than folding it into an existing one, so this project's
existing "take Squiggle's roundname verbatim as Round.name" ingestion path
(app/ingestion/fixtures.py, app/providers/afl/squiggle.py) requires ZERO
changes — RoundKind.WILDCARD_FINAL just needs its own entry in
ROUND_NAME_BY_FINALS_KIND below, pointing at that exact string. Squiggle's
own numeric `is_final` field (0-7, per its documented API) is NOT consumed
anywhere in this project's ingestion code — only the `roundname` string
is — so no `is_final`-based branch belongs in the fixture-ingestion path;
that field is purely external documentation confirming the finals-kind
semantics.
"""

from dataclasses import dataclass
from enum import Enum


class RoundKind(str, Enum):
    HOME_AND_AWAY = "home_and_away"
    WILDCARD_FINAL = "wildcard_final"  # AFL Tables' WF (2026+) - played before FINALS_WEEK_1
    FINALS_WEEK_1 = "finals_week_1"  # AFL Tables' EF or QF
    SEMI_FINALS = "semi_finals"
    PRELIMINARY_FINAL = "preliminary_final"
    GRAND_FINAL = "grand_final"


@dataclass(frozen=True)
class RoundLabel:
    raw: str  # as published, e.g. "R5", "EF"
    kind: RoundKind
    round_number: int | None  # only set for HOME_AND_AWAY; the source's own number, not a guess

    @property
    def is_final(self) -> bool:
        return self.kind is not RoundKind.HOME_AND_AWAY


# Squiggle's actual Round.name text for each finals kind — verified against
# real ingested data across every season 2016-2025 (identical every year),
# plus a live 2026 query for WILDCARD_FINAL (see module docstring:
# api.squiggle.com.au/?q=games;year=2026 round 25 -> roundname="Wildcard
# Finals", is_final=7).
ROUND_NAME_BY_FINALS_KIND: dict[RoundKind, str] = {
    RoundKind.WILDCARD_FINAL: "Wildcard Finals",
    RoundKind.FINALS_WEEK_1: "Finals Week 1",
    RoundKind.SEMI_FINALS: "Semi-Finals",
    RoundKind.PRELIMINARY_FINAL: "Preliminary Finals",
    RoundKind.GRAND_FINAL: "Grand Final",
}

_FINALS_KIND_BY_CODE: dict[str, RoundKind] = {
    "WF": RoundKind.WILDCARD_FINAL,
    "EF": RoundKind.FINALS_WEEK_1,
    "QF": RoundKind.FINALS_WEEK_1,
    "SF": RoundKind.SEMI_FINALS,
    "PF": RoundKind.PRELIMINARY_FINAL,
    "GF": RoundKind.GRAND_FINAL,
}


def parse_round_label(raw: str) -> RoundLabel | None:
    """Returns None for a label this project doesn't recognise (e.g. a
    future/unseen finals code, or a malformed header) — callers must treat
    that as "can't safely resolve this row" and report it, never guess a
    meaning for an unrecognised label. Accepts numbered rounds either as
    published on the game-by-game grid ("R5", with the letter prefix) or as
    a bare number ("5") — both forms appear across this project's own
    fixtures/tests, so both are handled here rather than requiring every
    caller to strip the prefix itself."""
    text = raw.strip()
    kind = _FINALS_KIND_BY_CODE.get(text)
    if kind is not None:
        return RoundLabel(raw=text, kind=kind, round_number=None)
    numeric_part = text[1:] if text[:1] in ("R", "r") else text
    if numeric_part.isdigit():
        return RoundLabel(raw=text, kind=RoundKind.HOME_AND_AWAY, round_number=int(numeric_part))
    return None

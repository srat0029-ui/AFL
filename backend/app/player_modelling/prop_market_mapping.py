"""Maps a player-prop odds provider's own market vocabulary onto this
project's internal PlayerMarket/LineType/threshold representation (Section
4 of the automated-odds stage brief) — kept as its own module so a second
provider (a different market-key vocabulary entirely) only needs its own
mapping function, never a change to ingestion, storage, or Prop Insights.

Only disposals and goals are mapped, because those are the only markets
with a promoted projection model behind them (see market.py's PlayerMarket
enum and its note on TACKLES/MARKS having no model yet) — Section 4 asks
for "at minimum" disposals and goals, not for modelling every market a
provider happens to offer. A market key this module doesn't recognise is
reported as unsupported, never silently dropped without being counted (see
UnsupportedMarket) — that distinction feeds directly into the market-
availability audit (Section 24: "markets our models support but provider
does not offer" / "markets we cannot currently model").
"""

from dataclasses import dataclass

from app.player_modelling.market import LineType, PlayerMarket
from app.providers.types import PlayerPropQuote

# Selection text as different providers spell it, normalised to this
# project's own three-value vocabulary (see PlayerPropMarket.selection's
# docstring) — case-insensitive matching, since capitalisation is not a
# meaningful signal here and providers are not consistent about it.
_SELECTION_ALIASES: dict[str, str] = {
    "over": "over",
    "under": "under",
    "yes": "yes",
    "no": "no",
}


@dataclass(frozen=True)
class NormalizedProp:
    """The same quote, with market/line/threshold/selection translated into
    this project's own vocabulary — everything else (player_name, prices,
    bookmaker, timestamps) is carried through unchanged from PlayerPropQuote."""

    quote: PlayerPropQuote
    market: PlayerMarket
    line_type: LineType
    threshold: float
    selection: str  # "over" | "under" | "yes"


@dataclass(frozen=True)
class UnsupportedMarket:
    """A market key this project doesn't (yet) model, or an outcome this
    module can't safely interpret (e.g. "no" side of an anytime-goalscorer
    market has no probability our single-player hurdle/NB distribution
    computes a complement for in a way worth surfacing) — reported, not
    dropped silently, so a refresh/audit can say exactly what was skipped
    and why."""

    quote: PlayerPropQuote
    reason: str


# Provider market key -> (PlayerMarket, LineType, selection-if-fixed).
# selection-if-fixed is None for markets where the outcome's own "Over"/
# "Under" text determines the side (see _map_selection); it's set for
# markets that only ever have one meaningful side (anytime goalscorer is
# always "yes" - there is no complementary priced "no" side in the
# response this project treats as a real market to compare against).
_MARKET_KEY_MAP: dict[str, tuple[PlayerMarket, LineType, str | None]] = {
    "player_disposals": (PlayerMarket.DISPOSALS, LineType.OVER_UNDER, None),
    "player_disposals_over": (PlayerMarket.DISPOSALS, LineType.OVER_UNDER, "over"),
    "player_goal_scorer_anytime": (PlayerMarket.GOALS, LineType.MULTI_PLUS, "yes"),
    "player_goals_scored_over": (PlayerMarket.GOALS, LineType.OVER_UNDER, "over"),
}


def _map_selection(market_key: str, raw_selection: str, fixed_selection: str | None) -> str | None:
    if fixed_selection is not None:
        return fixed_selection
    return _SELECTION_ALIASES.get(raw_selection.strip().lower())


def normalize_prop_quote(quote: PlayerPropQuote) -> NormalizedProp | UnsupportedMarket:
    """Translate one raw provider quote into this project's internal market
    representation, or explain why it can't be (unrecognised market key,
    unrecognised selection text, or a market needing a threshold that
    didn't provide one)."""
    mapping = _MARKET_KEY_MAP.get(quote.market_key)
    if mapping is None:
        return UnsupportedMarket(quote=quote, reason=f"unrecognised/unmodelled market key {quote.market_key!r}")

    market, line_type, fixed_selection = mapping
    selection = _map_selection(quote.market_key, quote.selection, fixed_selection)
    if selection is None:
        return UnsupportedMarket(
            quote=quote, reason=f"unrecognised selection {quote.selection!r} for market {quote.market_key!r}"
        )
    if selection in ("under", "no"):
        # The complementary side of a paired market - real and useful for
        # devigging (see prop_odds_ingestion.py), but not itself a
        # standalone "prop" this project prices a model probability
        # against (there is no "under 24.5 disposals" model output
        # distinct from "over 24.5 disposals"'s complement). Still
        # returned as a NormalizedProp (not unsupported) so it gets
        # persisted and is available to pair against its "over" sibling.
        pass

    threshold = _resolve_threshold(market, line_type, quote.threshold, selection)
    if threshold is None:
        return UnsupportedMarket(
            quote=quote, reason=f"no usable threshold for market {quote.market_key!r} (point={quote.threshold!r})"
        )

    return NormalizedProp(quote=quote, market=market, line_type=line_type, threshold=threshold, selection=selection)


def _resolve_threshold(market: PlayerMarket, line_type: LineType, point: float | None, selection: str) -> float | None:
    if line_type is LineType.MULTI_PLUS:
        # "Anytime goalscorer" has no `point` at all - it's inherently "1+".
        return 1.0 if point is None else point
    # OVER_UNDER always needs the provider's point value as the threshold -
    # never guessed if the provider didn't supply one (a real over/under
    # market with no line doesn't make sense, so refuse rather than assume).
    return point

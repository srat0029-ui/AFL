"""Static AFL club reference data: abbreviation and brand colours.

These are fixed, public facts (not something any fixture/results provider
exposes), so they're kept here as a lookup rather than derived per-team.
Keys must match the team name strings Squiggle returns exactly — confirmed
against the live `?q=teams` endpoint for all 18 active clubs.
"""

# name -> (abbrev, primary_colour, secondary_colour)
TEAM_METADATA: dict[str, tuple[str, str, str]] = {
    "Adelaide": ("ADE", "#002B5C", "#E21937"),
    "Brisbane Lions": ("BRI", "#7A0C2E", "#FBB917"),
    "Carlton": ("CAR", "#0E1E2D", "#FFFFFF"),
    "Collingwood": ("COL", "#000000", "#FFFFFF"),
    "Essendon": ("ESS", "#CC2031", "#000000"),
    "Fremantle": ("FRE", "#2E0854", "#FFFFFF"),
    "Geelong": ("GEE", "#002B5C", "#FFFFFF"),
    "Gold Coast": ("GCS", "#D2001C", "#FFCD00"),
    "Greater Western Sydney": ("GWS", "#F57920", "#231F20"),
    "Hawthorn": ("HAW", "#4D2004", "#FFCC29"),
    "Melbourne": ("MEL", "#061A33", "#C62125"),
    "North Melbourne": ("NOR", "#013A9B", "#FFFFFF"),
    "Port Adelaide": ("POR", "#008C95", "#000000"),
    "Richmond": ("RIC", "#FFD200", "#000000"),
    "St Kilda": ("STK", "#ED0F05", "#000000"),
    "Sydney": ("SYD", "#E2231A", "#FFFFFF"),
    "West Coast": ("WCE", "#003087", "#F2A900"),
    "Western Bulldogs": ("WBD", "#00539F", "#E21937"),
}


def get_team_metadata(team_name: str) -> tuple[str, str | None, str | None]:
    """Returns (abbrev, primary_colour, secondary_colour).

    Falls back to a derived abbreviation and no colours for a team name we
    don't recognise (e.g. a historical/defunct club), rather than failing
    ingestion outright.
    """
    metadata = TEAM_METADATA.get(team_name)
    if metadata is not None:
        return metadata
    return team_name[:3].upper(), None, None

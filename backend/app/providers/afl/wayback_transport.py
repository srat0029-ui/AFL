"""Wayback Machine-backed transport for AFL Tables URLs — a resilience
fallback for when direct access to afltables.com is blocked by the site's
own bot-mitigation. Observed persistently during the player-data-foundation
stage and again on a later real run: every direct request — with or
without a custom User-Agent, on different days — returns a 302 to a
malformed/garbled Location header, a soft-block pattern rather than a
transient outage. Fetches the same real, unmodified pages from a different
host entirely (archive.org), adding zero extra load to afltables.com
itself — this is exactly what the Wayback Machine exists for.

Not the default transport for AFLTablesStatsProvider or
AFLTablesPlayerStatsProvider — both still default to fetching afltables.com
directly, since that's the correct behaviour if/when direct access
recovers. Pass transport=wayback_transport explicitly, or use
`python -m app.ingestion.cli --source wayback` (see app/ingestion/cli.py),
when direct access is failing.
"""

import os
import re
import subprocess
from datetime import date

_YEAR_RE = re.compile(r"(20\d{2})")


def _target_wayback_year(url: str) -> int:
    """Picks a year to anchor the Wayback "closest snapshot" lookup: the
    year after whatever season year appears in the URL, so a full,
    completed season is captured rather than a mid-season snapshot missing
    later rounds. Wayback's own shorthand resolves to the closest actual
    snapshot regardless (it will happily return a snapshot from well before
    this target if that's all that exists, e.g. for the current
    in-progress season), so this is a starting point, not a hard
    requirement. Falls back to today's year if no year-like sequence is
    found in the URL at all."""
    match = _YEAR_RE.search(url)
    if match:
        return int(match.group(1)) + 1
    return date.today().year


def wayback_transport(url: str, timeout: float = 40.0) -> tuple[int, str, str]:
    """Same (status_code, content_type, body) contract as
    app/providers/afl/afltables.py's curl_transport(), for a real
    afltables.com URL. Two clean steps rather than `curl -i -L` in one
    shot: resolve the final URL first (no body captured), then fetch that
    exact URL once with no further redirects — so there's only ever one
    header/body boundary to split on, instead of one stacked per hop
    (which -i -L would produce, and which can't be split reliably since an
    HTML body can itself contain blank lines).
    """
    year = _target_wayback_year(url)
    wayback_url = f"https://web.archive.org/web/{year}/{url}"

    try:
        resolve = subprocess.run(
            ["curl", "-s", "-o", os.devnull, "-w", "%{url_effective}", "-L", "--max-time", str(timeout), wayback_url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "wayback_transport requires the `curl` command-line tool (bundled with Windows 10+ and most Linux/macOS installs)."
        ) from exc
    if resolve.returncode != 0 or not resolve.stdout.startswith("http"):
        return 0, "", ""
    final_url = resolve.stdout.strip()

    result = subprocess.run(
        ["curl", "-s", "-i", "--max-time", str(timeout), final_url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    if result.returncode != 0:
        return 0, "", ""
    # text=True applies universal-newline translation, so curl's \r\n
    # becomes \n; only one hop now (the redirect was already resolved
    # above), so the first blank line is unambiguously the header/body
    # boundary.
    header_block, _, body = result.stdout.partition("\n\n")
    status_code = 0
    for line in header_block.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                status_code = int(parts[1])
    return status_code, "text/html", body

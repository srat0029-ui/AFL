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
import subprocess
from datetime import date

# Wayback's "closest snapshot" shorthand (web.archive.org/web/{year}/{url})
# resolves to whichever real capture is closest in time to this anchor - not
# necessarily the latest one. A real bug found via the 2016-2025 rerun: for a
# team-season URL anchored on season_year + 1, archive.org sometimes has no
# capture near that date at all, and "closest" resolves BACKWARD to a
# mid-season capture from years earlier (e.g. Carlton's 2017 page: the
# closest-to-2018 capture on file was from July 2017, mid-season, missing
# rounds 19-25 and every final - even though complete, full-season captures
# of that exact page exist from 2023, Dec 2025 and Jan 2026, per
# http://web.archive.org/cdx/search/cdx?url=...&output=json). A fixed
# far-future anchor sidesteps this entirely: since "closest" is symmetric
# but every real capture is necessarily in the past, anchoring beyond any
# capture that will ever exist guarantees the LATEST available capture wins
# for every URL, every time - which is also correct for a current,
# still-in-progress season (latest-available is the most complete option
# there too).
_FAR_FUTURE_ANCHOR_YEAR = 2099


def _target_wayback_year(url: str) -> int:
    return _FAR_FUTURE_ANCHOR_YEAR


def _run_curl(args: list[str], timeout: float):
    """subprocess.run wrapper shared by both steps below. curl's own
    --max-time is meant to bound each request, but on Windows a hung/stalled
    connection can still leave the subprocess pipe-reading thread blocked
    past that (a real crash caught on a live run: subprocess.run's *own*
    timeout=... raised TimeoutExpired from deep inside communicate(), with
    nothing catching it — it took the whole CLI process down mid-backfill).
    Returns None on any failure to start/complete the process (missing
    curl, timeout, or anything else) so every caller has exactly one
    "this attempt failed, safe to retry" signal to check, matching how a
    failed HTTP fetch is already handled everywhere else in this module.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError as exc:
        raise RuntimeError(
            "wayback_transport requires the `curl` command-line tool (bundled with Windows 10+ and most Linux/macOS installs)."
        ) from exc


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

    resolve = _run_curl(
        ["curl", "-s", "-o", os.devnull, "-w", "%{url_effective}", "-L", "--max-time", str(timeout), wayback_url],
        timeout,
    )
    if resolve is None or resolve.returncode != 0 or not resolve.stdout.startswith("http"):
        return 0, "", ""
    final_url = resolve.stdout.strip()

    result = _run_curl(["curl", "-s", "-i", "--max-time", str(timeout), final_url], timeout)
    if result is None or result.returncode != 0:
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
